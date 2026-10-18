import json
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest
from stack import TopicTap, post_activity

FIXTURE = Path(__file__).parent / "fixtures" / "chargeback.json"


@pytest.fixture
def activity_recorded() -> Iterator[TopicTap]:
    tap = TopicTap("activity-recorded")
    yield tap
    tap.delete()


def test_us1_recorded_activity_published_once(activity_recorded: TopicTap) -> None:
    body = json.loads(FIXTURE.read_text()) | {
        "activityId": f"a-{uuid4()}",
        "merchantId": f"m-{uuid4()}",
    }

    created_status, created = post_activity(body)
    repeat_status, repeat = post_activity(body)
    messages = activity_recorded.messages(
        lambda event: event["activityId"] == body["activityId"], wait=5
    )

    assert (created_status, repeat_status) == (201, 200)
    assert repeat == created
    assert len(messages) == 1
    event = json.loads(messages[0].data)
    assert {key: event[key] for key in body} == body | {"occurredAt": "2026-10-01T10:00:00+00:00"}
    assert messages[0].attributes["eventId"] == event["eventId"]
    assert messages[0].ordering_key == body["merchantId"]
