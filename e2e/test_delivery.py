import contextlib
import json
import subprocess
import time
from collections.abc import Iterator
from uuid import uuid4

import pytest
from stack import TopicTap, activity, compose, get_segment, post_activity, wait_for


@pytest.fixture
def pubsub_paused() -> Iterator[None]:
    compose("pause", "pubsub")
    try:
        yield
    finally:
        with contextlib.suppress(subprocess.CalledProcessError):
            compose("unpause", "pubsub")


@pytest.fixture
def new_consumer() -> Iterator[TopicTap]:
    tap = TopicTap("activity-recorded")
    yield tap
    tap.delete()


def test_2_pubsub_down_post_201_published_after(pubsub_paused: None) -> None:
    merchant = f"m-{uuid4()}"

    status, _ = post_activity(activity(merchant, "chargeback", "2026-10-01T10:00:00Z"))
    time.sleep(2)
    unscored, _ = get_segment(merchant)
    compose("unpause", "pubsub")
    segment = wait_for(lambda: get_segment(merchant), lambda r: r[0] == 200, "scored")[1]

    assert status == 201
    assert unscored == 404
    assert segment["band"] == "medium"


def test_new_subscription_receives_events_no_producer_change(new_consumer: TopicTap) -> None:
    merchant = f"m-{uuid4()}"
    body = activity(merchant, "chargeback", "2026-10-01T10:00:00Z")

    assert post_activity(body)[0] == 201
    received = new_consumer.messages(lambda event: event["activityId"] == body["activityId"], 5)
    segment = wait_for(lambda: get_segment(merchant), lambda r: r[0] == 200, "scored")[1]

    assert [json.loads(m.data)["merchantId"] for m in received] == [merchant]
    assert segment["band"] == "medium"
