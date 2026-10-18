import random
import threading
import time
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import Connection, select

from segmenter.adapters.outbox import OutboxMessage, SqlOutbox
from segmenter.adapters.processed_events import SqlProcessedEvents
from segmenter.adapters.tables import outbox, processed_events
from segmenter.service_layer.cleanup import clean_processed_events
from segmenter.service_layer.relay import clean_outbox, relay_once

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
) -> UUID:
    event_id = uuid4()
    connection.execute(
        outbox.insert().values(
            event_id=event_id,
            topic="segmenter-events",
            ordering_key=merchant_id,
            body={"eventId": str(event_id), "eventType": "RiskSegmentChanged"},
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


def test_3_each_merchant_published_in_id_order(connection: Connection) -> None:
    merchants = [f"m-{uuid4()}" for _ in range(5)]
    expected: dict[str, list[UUID]] = {merchant: [] for merchant in merchants}
    for n in range(50):
        merchant = merchants[n % 5]
        # created_at runs backwards, so reading by created_at instead of id reverses the order.
        expected[merchant].append(add_row(connection, merchant, NOW - timedelta(seconds=n)))
    publisher = RecordingPublisher()

    relay_once(SqlOutbox(connection), publisher, NOW)

    published = {
        merchant: [m.event_id for m in publisher.published if m.ordering_key == merchant]
        for merchant in merchants
    }
    assert published == expected
    assert unsent_ids(connection, [e for ids in expected.values() for e in ids]) == []


def test_3_crash_before_sent_at_republishes_same_event_id(connection: Connection) -> None:
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


def test_40_cleanup_deletes_processed_events_over_14_days(connection: Connection) -> None:
    old, recent = uuid4(), uuid4()
    store = SqlProcessedEvents(connection)
    store.add(old, NOW - timedelta(days=14, seconds=1))
    store.add(recent, NOW - timedelta(days=13))

    clean_processed_events(store, NOW)

    left = set(
        connection.scalars(
            select(processed_events.c.event_id).where(
                processed_events.c.event_id.in_([old, recent])
            )
        )
    )
    assert left == {recent}
