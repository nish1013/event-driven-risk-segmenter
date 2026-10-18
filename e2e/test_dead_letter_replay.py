import json
import time
from collections.abc import Iterator
from typing import Any
from uuid import uuid4

import pytest
from google.cloud import pubsub_v1
from stack import (
    PROJECT_ID,
    TopicTap,
    activity,
    compose,
    get_alerts,
    get_segment,
    post_activity,
    psql,
    wait_for,
)

SEGMENTER_SUBSCRIPTION = "segmenter-activity-recorded"
NOTIFIER_SUBSCRIPTION = "notifier-segmenter-events"
QUIET_S = 10.0


class Poison:
    """A fault that fails every Segmenter transaction storing one activity, until removed."""

    def __init__(self, activity_id: str) -> None:
        self.name = f"segmenter.e2e_poison_{uuid4().hex[:8]}"
        trigger = self.name.split(".")[1]
        psql(
            f"CREATE FUNCTION {self.name}() RETURNS trigger LANGUAGE plpgsql AS "
            f"$$ BEGIN RAISE EXCEPTION 'poisoned %', NEW.activity_id; END $$; "
            f"CREATE TRIGGER {trigger} BEFORE INSERT ON segmenter.facts FOR EACH ROW "
            f"WHEN (NEW.activity_id = '{activity_id}') EXECUTE FUNCTION {self.name}();"
        )

    def remove(self) -> None:
        trigger = self.name.split(".")[1]
        psql(
            f"DROP TRIGGER IF EXISTS {trigger} ON segmenter.facts; "
            f"DROP FUNCTION IF EXISTS {self.name}();"
        )


@pytest.fixture
def subscriber() -> Iterator[pubsub_v1.SubscriberClient]:
    client = pubsub_v1.SubscriberClient()
    yield client
    client.close()


@pytest.fixture
def activity_recorded() -> Iterator[TopicTap]:
    tap = TopicTap("activity-recorded")
    yield tap
    tap.delete()


@pytest.fixture
def segmenter_events() -> Iterator[TopicTap]:
    tap = TopicTap("segmenter-events")
    yield tap
    tap.delete()


def test_us10_poison_parked_later_events_flow_replay_applies_once(
    subscriber: pubsub_v1.SubscriberClient, activity_recorded: TopicTap
) -> None:
    merchant = f"m-{uuid4()}"
    chargeback = activity(merchant, "chargeback", "2026-10-01T10:00:00Z")
    later = activity(merchant, "failed_login", "2026-10-02T10:00:00Z")
    poison = Poison(chargeback["activityId"])
    try:
        assert post_activity(chargeback)[0] == 201
        assert post_activity(later)[0] == 201
        parked = wait_for(
            lambda: peek(subscriber, SEGMENTER_SUBSCRIPTION, chargeback["activityId"]),
            bool,
            "chargeback dead-lettered",
        )
        while_parked = wait_for(lambda: get_segment(merchant), lambda r: r[0] == 200, "scored")[1]
    finally:
        poison.remove()

    replay("segmenter-worker", SEGMENTER_SUBSCRIPTION)
    after = wait_for(lambda: get_segment(merchant)[1], lambda s: s["band"] == "medium", "medium")
    alerts = wait_for(lambda: get_alerts(merchant), bool, "segment_changed alert")
    copies = activity_recorded.messages(
        lambda event: event["activityId"] == chargeback["activityId"], wait=5
    )

    assert len(parked) == 1
    assert parked[0].attributes["CloudPubSubDeadLetterSourceDeliveryCount"] == "5"
    assert (while_parked["band"], while_parked["rule"]) == ("low", None)
    assert peek(subscriber, SEGMENTER_SUBSCRIPTION, chargeback["activityId"]) == []
    assert (after["band"], after["rule"]) == ("medium", "chargeback_1_in_30d")
    assert [(a["kind"], a["details"]["from"], a["details"]["to"]) for a in alerts] == [
        ("segment_changed", "low", "medium")
    ]
    event_ids = {(m.attributes["eventId"], json.loads(m.data)["eventId"]) for m in copies}
    assert len(copies) == 2, "the original and its replay"
    assert event_ids == {(parked[0].attributes["eventId"],) * 2}
    assert {m.ordering_key for m in copies} == {merchant}


def test_us10_replayed_event_already_applied_changes_nothing(segmenter_events: TopicTap) -> None:
    merchant = f"m-{uuid4()}"
    assert post_activity(activity(merchant, "chargeback", "2026-10-01T10:00:00Z"))[0] == 201
    applied = wait_for(lambda: get_alerts(merchant), bool, "segment_changed alert")
    [changed] = segmenter_events.messages(lambda event: event["merchantId"] == merchant, wait=5)

    park(changed, NOTIFIER_SUBSCRIPTION)
    replay("notifier-worker", NOTIFIER_SUBSCRIPTION)
    copies = segmenter_events.messages(lambda event: event["merchantId"] == merchant, wait=5)
    deadline = time.monotonic() + QUIET_S
    while time.monotonic() < deadline and len(get_alerts(merchant)) == 1:
        time.sleep(0.5)

    assert get_alerts(merchant) == applied
    assert [(m.attributes["eventId"], m.data) for m in copies] == [
        (changed.attributes["eventId"], changed.data)
    ]


def replay(service: str, subscription: str) -> None:
    module = f"{service.split('-')[0]}.entrypoints.replay"
    compose("exec", "-T", service, "python", "-m", module, "--subscription", subscription)


def park(message: pubsub_v1.types.PubsubMessage, subscription: str) -> None:
    """Puts an applied message on the dead-letter topic, as five lost acks would."""
    publisher = pubsub_v1.PublisherClient(
        publisher_options=pubsub_v1.types.PublisherOptions(enable_message_ordering=True)
    )
    publisher.publish(
        publisher.topic_path(PROJECT_ID, f"{subscription}-dead-letter"),
        message.data,
        ordering_key=message.ordering_key,
        **dict(message.attributes),
    ).result(timeout=30)


def peek(
    subscriber: pubsub_v1.SubscriberClient, subscription: str, activity_id: str
) -> list[pubsub_v1.types.PubsubMessage]:
    """The dead-lettered copies of one activity's event, left in place."""
    path = subscriber.subscription_path(PROJECT_ID, f"{subscription}-dead-letter")
    received: list[Any] = []
    while batch := subscriber.pull(
        subscription=path, max_messages=100, timeout=2
    ).received_messages:
        received += batch
    if received:
        subscriber.modify_ack_deadline(
            subscription=path, ack_ids=[r.ack_id for r in received], ack_deadline_seconds=0
        )
    return [
        r.message for r in received if json.loads(r.message.data).get("activityId") == activity_id
    ]
