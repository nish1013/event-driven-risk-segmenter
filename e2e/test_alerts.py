import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from stack import compose, get_alerts, get_segment, post_activity, wait_for

FIXTURE = Path(__file__).parent / "fixtures" / "chargeback.json"


@pytest.fixture
def notifier_stopped() -> Iterator[None]:
    compose("stop", "notifier-worker")
    try:
        yield
    finally:
        compose("start", "notifier-worker")


def post_chargeback(merchant: str) -> None:
    body = json.loads(FIXTURE.read_text()) | {"activityId": f"a-{uuid4()}", "merchantId": merchant}
    assert post_activity(body)[0] == 201


def wait_for_band(merchant: str, expected: str) -> str | None:
    return wait_for(lambda: band(merchant), lambda b: b == expected, f"{merchant} {expected}")


def wait_for_alerts(merchant: str) -> list[dict[str, Any]]:
    return wait_for(lambda: get_alerts(merchant), bool, f"{merchant} alert")


def band(merchant: str) -> str | None:
    status, body = get_segment(merchant)
    return body["band"] if status == 200 else None


def test_us6_chargeback_band_medium_then_alert() -> None:
    merchant = f"m-{uuid4()}"

    post_chargeback(merchant)

    wait_for_band(merchant, "medium")
    alerts = wait_for_alerts(merchant)
    assert [(a["kind"], a["status"], a["details"]["from"], a["details"]["to"]) for a in alerts] == [
        ("segment_changed", None, "low", "medium")
    ]
    assert get_alerts(merchant, "open") == []


def test_us6_notifier_stopped_bands_kept_alerts_after_restart(
    notifier_stopped: None,
) -> None:
    merchants = [f"m-{uuid4()}" for _ in range(3)]

    for merchant in merchants:
        post_chargeback(merchant)
    bands_while_stopped = [wait_for_band(m, "medium") for m in merchants]
    alerts_while_stopped = [get_alerts(m) for m in merchants]
    compose("start", "notifier-worker")
    alerts = [wait_for_alerts(m) for m in merchants]

    assert bands_while_stopped == ["medium"] * 3
    assert alerts_while_stopped == [[], [], []]
    assert [[a["kind"] for a in found] for found in alerts] == [["segment_changed"]] * 3
    assert [band(m) for m in merchants] == ["medium"] * 3
