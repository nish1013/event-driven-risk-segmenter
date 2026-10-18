import json
import os
import time
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest
from google.cloud import pubsub_v1
from stack import post_activity

FIXTURE = Path(__file__).parent / "fixtures" / "chargeback.json"


class TopicTap:
    def __init__(self, project_id: str, topic: str) -> None:
        self._subscriber = pubsub_v1.SubscriberClient()
        self._subscription = self._subscriber.subscription_path(
            project_id, f"e2e-tap-{uuid4().hex}"
        )
        self._subscriber.create_subscription(
            request={
                "name": self._subscription,
                "topic": f"projects/{project_id}/topics/{topic}",
            }
        )

    def messages_for(self, activity_id: str, wait: float) -> list[pubsub_v1.types.PubsubMessage]:
        found: list[pubsub_v1.types.PubsubMessage] = []
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline:
            response = self._subscriber.pull(
                subscription=self._subscription, max_messages=10, timeout=2
            )
            if response.received_messages:
                self._subscriber.acknowledge(
                    subscription=self._subscription,
                    ack_ids=[received.ack_id for received in response.received_messages],
                )
            found += [
                received.message
                for received in response.received_messages
                if json.loads(received.message.data)["activityId"] == activity_id
            ]
        return found

    def delete(self) -> None:
        self._subscriber.delete_subscription(subscription=self._subscription)


@pytest.fixture
def activity_recorded() -> Iterator[TopicTap]:
    tap = TopicTap(os.environ["PUBSUB_PROJECT_ID"], "activity-recorded")
    yield tap
    tap.delete()


def test_us1_recorded_activity_published_once(activity_recorded: TopicTap) -> None:
    body = json.loads(FIXTURE.read_text()) | {"activityId": f"a-{uuid4()}"}

    created_status, created = post_activity(body)
    repeat_status, repeat = post_activity(body)
    messages = activity_recorded.messages_for(body["activityId"], wait=5)

    assert (created_status, repeat_status) == (201, 200)
    assert repeat == created
    assert len(messages) == 1
    event = json.loads(messages[0].data)
    assert {key: event[key] for key in body} == body | {"occurredAt": "2026-10-01T10:00:00+00:00"}
    assert messages[0].attributes["eventId"] == event["eventId"]
    assert messages[0].ordering_key == body["merchantId"]
