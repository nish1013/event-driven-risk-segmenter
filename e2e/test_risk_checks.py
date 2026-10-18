import time
from collections.abc import Callable
from typing import Any
from uuid import uuid4

import pytest
from stack import get_alerts, get_segment, post_activity, post_resolution

SCORING_TIMEOUT_S = 30.0


def activity(merchant: str, activity_type: str, occurred_at: str) -> dict[str, Any]:
    payloads: dict[str, dict[str, str]] = {
        "chargeback": {
            "transactionId": f"t-{uuid4()}",
            "amount": "120.00",
            "currency": "GBP",
            "reasonCode": "4837",
        },
        "failed_login": {"userId": "u-1", "method": "password"},
    }
    return {
        "activityId": f"a-{uuid4()}",
        "merchantId": merchant,
        "type": activity_type,
        "occurredAt": occurred_at,
        "payload": payloads[activity_type],
    }


def wait_until(merchant: str, done: Callable[[dict[str, Any]], bool]) -> dict[str, Any]:
    deadline = time.monotonic() + SCORING_TIMEOUT_S
    while time.monotonic() < deadline:
        status, body = get_segment(merchant)
        if status == 200 and done(body):
            return body
        time.sleep(0.5)
    pytest.fail(f"{merchant} did not reach the expected segment in {SCORING_TIMEOUT_S}s")


def wait_for_alerts(merchant: str, status: str) -> list[dict[str, Any]]:
    deadline = time.monotonic() + SCORING_TIMEOUT_S
    while time.monotonic() < deadline:
        alerts = get_alerts(merchant, status)
        if alerts:
            return alerts
        time.sleep(0.5)
    pytest.fail(f"no {status} alert for {merchant} in {SCORING_TIMEOUT_S}s")


def test_us3_us4_us8_drop_held_approved_alert_closed() -> None:
    merchant = f"m-{uuid4()}"
    for day in ("2026-09-01", "2026-09-05", "2026-09-10"):
        assert post_activity(activity(merchant, "chargeback", f"{day}T10:00:00Z"))[0] == 201
    # Delivery order is best-effort (#43): wait for the rise before sending the drop.
    wait_until(merchant, lambda s: s["band"] == "high")

    drop = activity(merchant, "failed_login", "2026-10-17T10:00:00Z")
    assert post_activity(drop)[0] == 201
    held = wait_until(merchant, lambda s: s["openCheck"] is not None)
    check_id = held["openCheck"]["checkId"]
    open_alerts = wait_for_alerts(merchant, "open")
    status, resolved = post_resolution(
        check_id,
        {"decision": "approve_lower", "officerId": "o-7", "reason": "chargebacks settled"},
    )
    after = get_segment(merchant)[1]
    closed = wait_for_alerts(merchant, "closed")

    assert (held["band"], held["openCheck"]["proposedBand"]) == ("high", "low")
    assert (status, resolved["decision"], resolved["band"]) == (200, "approve_lower", "low")
    assert (after["band"], after["openCheck"]) == ("low", None)
    assert [(a["checkId"], a["kind"]) for a in open_alerts] == [(check_id, "risk_check_required")]
    assert [(a["checkId"], a["decision"]) for a in closed] == [(check_id, "approve_lower")]
    assert get_alerts(merchant, "open") == []
