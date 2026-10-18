import json
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from stack import compose, get_segment, post_activity

FIXTURE = Path(__file__).parent / "fixtures" / "chargeback.json"
SCORING_TIMEOUT_S = 30.0


@pytest.fixture
def segmenter_stopped() -> Iterator[None]:
    compose("stop", "segmenter-worker")
    try:
        yield
    finally:
        compose("start", "segmenter-worker")


def wait_for_segment(merchant_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + SCORING_TIMEOUT_S
    while time.monotonic() < deadline:
        status, body = get_segment(merchant_id)
        if status == 200:
            return body
        time.sleep(0.5)
    pytest.fail(f"{merchant_id} not scored within {SCORING_TIMEOUT_S}s")


def test_us2_chargeback_scored_medium() -> None:
    merchant = f"m-{uuid4()}"
    body = json.loads(FIXTURE.read_text()) | {"activityId": f"a-{uuid4()}", "merchantId": merchant}

    post_activity(body)

    segment = wait_for_segment(merchant)
    assert (segment["band"], segment["rule"], segment["policyVersion"]) == (
        "medium",
        "chargeback_1_in_30d",
        1,
    )


def test_us1_segmenter_stopped_post_fast_scored_after_restart(
    segmenter_stopped: None,
) -> None:
    merchant = f"m-{uuid4()}"
    body = json.loads(FIXTURE.read_text()) | {"activityId": f"a-{uuid4()}", "merchantId": merchant}

    started = time.monotonic()
    status, _ = post_activity(body)
    elapsed = time.monotonic() - started
    time.sleep(2)
    unscored, _ = get_segment(merchant)
    compose("start", "segmenter-worker")
    segment = wait_for_segment(merchant)

    assert status == 201
    assert elapsed < 0.2, f"POST took {elapsed:.3f}s"
    assert unscored == 404
    assert segment["band"] == "medium"
