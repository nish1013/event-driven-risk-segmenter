import contextlib
import json
import os
import random
import threading
import time
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from google.api_core.exceptions import NotFound
from google.cloud import pubsub_v1
from sqlalchemy import Connection, select

from recording.adapters.outbox import OutboxMessage, SqlOutbox
from recording.adapters.pubsub_publisher import PubSubPublisher
from recording.adapters.tables import activities, outbox
from recording.service_layer.relay import clean_outbox, relay_once

NOW = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)


class RecordingPublisher:
    def __init__(self) -> None:
        self.published: list[OutboxMessage] = []
        self._lock = threading.Lock()

    def publish(self, messages: Sequence[OutboxMessage]) -> list[bool]:
        for message in messages:
            # Jitter makes any concurrent publishing show up as a reordering.
            time.sleep(random.uniform(0, 0.002))
            with self._lock:
                self.published.append(message)
        return [True] * len(messages)


def add_row(
    connection: Connection,
    merchant_id: str,
    created_at: datetime = NOW,
    sent_at: datetime | None = None,
    topic: str = "activity-recorded",
) -> UUID:
    activity_id, event_id = f"a-{uuid4()}", uuid4()
    connection.execute(
        activities.insert().values(
            activity_id=activity_id,
            merchant_id=merchant_id,
            type="failed_login",
            occurred_at=created_at,
            payload={"userId": "u-1", "method": "password"},
            recorded_at=created_at,
        )
    )
    connection.execute(
        outbox.insert().values(
            event_id=event_id,
            activity_id=activity_id,
            topic=topic,
            ordering_key=merchant_id,
            body={"eventId": str(event_id), "eventType": "ActivityRecorded"},
            created_at=created_at,
            sent_at=sent_at,
        )
    )
    return event_id


def unsent_ids(connection: Connection, event_ids: list[UUID]) -> list[UUID]:
    return list(
        connection.scalars(
            select(outbox.c.event_id)
            .where(outbox.c.event_id.in_(event_ids), outbox.c.sent_at.is_(None))
            .order_by(outbox.c.id)
        )
    )


class EmulatorTopic:
    def __init__(self, project_id: str) -> None:
        self.name = f"recording-it-{uuid4().hex}"
        self._publisher = pubsub_v1.PublisherClient()
        self._subscriber = pubsub_v1.SubscriberClient()
        self._topic = self._publisher.topic_path(project_id, self.name)
        self._subscription = self._subscriber.subscription_path(project_id, self.name)

    def create(self) -> None:
        self._publisher.create_topic(name=self._topic)
        self._subscriber.create_subscription(
            request={
                "name": self._subscription,
                "topic": self._topic,
                "enable_message_ordering": True,
            }
        )

    def pull_one(self) -> pubsub_v1.types.PubsubMessage:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            response = self._subscriber.pull(
                subscription=self._subscription, max_messages=1, timeout=5
            )
            for received in response.received_messages:
                self._subscriber.acknowledge(
                    subscription=self._subscription, ack_ids=[received.ack_id]
                )
                return received.message
        pytest.fail(f"no message on {self.name}")

    def delete(self) -> None:
        with contextlib.suppress(NotFound):
            self._subscriber.delete_subscription(subscription=self._subscription)
        with contextlib.suppress(NotFound):
            self._publisher.delete_topic(topic=self._topic)


@pytest.fixture
def emulator_topic() -> Iterator[EmulatorTopic]:
    topic = EmulatorTopic(os.environ["PUBSUB_PROJECT_ID"])
    yield topic
    topic.delete()


def test_3_each_merchant_published_in_id_order(connection: Connection) -> None:
    merchants = [f"m-{uuid4()}" for _ in range(5)]
    expected: dict[str, list[UUID]] = {merchant: [] for merchant in merchants}
    for n in range(50):
        merchant = merchants[n % 5]
        # created_at runs backwards, so reading by created_at instead of id reverses the order.
        event_id = add_row(connection, merchant, created_at=NOW - timedelta(seconds=n))
        expected[merchant].append(event_id)
    publisher = RecordingPublisher()

    relay_once(SqlOutbox(connection), publisher, NOW)

    published = {
        merchant: [m.event_id for m in publisher.published if m.ordering_key == merchant]
        for merchant in merchants
    }
    assert published == expected
    assert unsent_ids(connection, [e for ids in expected.values() for e in ids]) == []


def test_i4_crash_before_sent_at_republishes_same_event_id(connection: Connection) -> None:
    event_id = add_row(connection, f"m-{uuid4()}")
    publisher = RecordingPublisher()

    crashed = connection.begin_nested()
    relay_once(SqlOutbox(connection), publisher, NOW)
    crashed.rollback()
    relay_once(SqlOutbox(connection), publisher, NOW)

    assert [m.event_id for m in publisher.published if m.event_id == event_id] == [
        event_id,
        event_id,
    ]
    assert unsent_ids(connection, [event_id]) == []


def test_40_cleanup_deletes_rows_sent_over_7_days_ago(connection: Connection) -> None:
    merchant = f"m-{uuid4()}"
    old = add_row(connection, merchant, sent_at=NOW - timedelta(days=7, seconds=1))
    recent = add_row(connection, merchant, sent_at=NOW - timedelta(days=6))
    unsent = add_row(connection, merchant, created_at=NOW - timedelta(days=30))

    clean_outbox(SqlOutbox(connection), NOW)

    left = set(
        connection.scalars(select(outbox.c.event_id).where(outbox.c.ordering_key == merchant))
    )
    assert left == {recent, unsent}
    assert old not in left


def test_us1_publish_refused_then_published_on_recovery(
    connection: Connection, emulator_topic: EmulatorTopic
) -> None:
    merchant = f"m-{uuid4()}"
    event_id = add_row(connection, merchant, topic=emulator_topic.name)
    publisher = PubSubPublisher.from_env()

    relay_once(SqlOutbox(connection), publisher, NOW)
    left_after_refusal = unsent_ids(connection, [event_id])
    emulator_topic.create()
    relay_once(SqlOutbox(connection), publisher, NOW)

    assert left_after_refusal == [event_id]
    assert unsent_ids(connection, [event_id]) == []
    message = emulator_topic.pull_one()
    assert message.ordering_key == merchant
    assert dict(message.attributes) == {"eventId": str(event_id), "eventType": "ActivityRecorded"}
    assert json.loads(message.data) == {"eventId": str(event_id), "eventType": "ActivityRecorded"}
