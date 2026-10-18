from concurrent.futures import ThreadPoolExecutor
from typing import Any

from segmenter_db import api, deliver, events, recorded, segment_row, wait_for_lock_waiters
from sqlalchemy import Engine, select

from segmenter.adapters.tables import checks, segments

UPDATE_SEGMENT = "UPDATE segmenter.segments"


def check_rows(engine: Engine, merchant_id: str) -> list[dict[str, Any]]:
    with engine.connect() as connection:
        return [
            dict(row)
            for row in connection.execute(
                select(checks)
                .where(checks.c.merchant_id == merchant_id)
                .order_by(checks.c.opened_at)
            ).mappings()
        ]


def open_checks(engine: Engine, merchant_id: str) -> list[dict[str, Any]]:
    return [row for row in check_rows(engine, merchant_id) if row["status"] == "open"]


def kinds(engine: Engine, merchant_id: str) -> list[str]:
    return [body["eventType"] for body in events(engine, merchant_id)]


def high_by_chargebacks(engine: Engine, merchant_id: str) -> None:
    for day in ("2026-09-01", "2026-09-05", "2026-09-10"):
        deliver(engine, recorded(merchant_id, "chargeback", f"{day}T00:00"))


def held_drop(engine: Engine, merchant_id: str) -> str:
    high_by_chargebacks(engine, merchant_id)
    deliver(engine, recorded(merchant_id, "failed_login", "2026-10-20T00:00"))
    [check] = open_checks(engine, merchant_id)
    return str(check["check_id"])


def resolution(decision: str, reason: str = "reviewed") -> dict[str, str]:
    return {"decision": decision, "officerId": "o-7", "reason": reason}


def test_i7_concurrent_lower_recomputes_one_open_check(engine: Engine, merchant: str) -> None:
    high_by_chargebacks(engine, merchant)
    drops = [
        recorded(merchant, "failed_login", "2026-10-20T00:00"),
        recorded(merchant, "failed_login", "2026-10-21T00:00"),
    ]

    # Hold the segment row so both workers decide "no open check" before either writes.
    with ThreadPoolExecutor(2) as pool, engine.connect() as holder:
        holder.begin()
        holder.execute(select(segments).where(segments.c.merchant_id == merchant).with_for_update())
        futures = [pool.submit(deliver, engine, drop) for drop in drops]
        raced = wait_for_lock_waiters(engine, UPDATE_SEGMENT, 2)
        holder.rollback()
        for future in futures:
            future.result()

    assert raced, "workers did not both reach the update"
    assert len(open_checks(engine, merchant)) == 1
    assert kinds(engine, merchant).count("ManualRiskCheckRequired") == 1
    assert segment_row(engine, merchant)[0] == "high"


def test_i9_concurrent_resolves_one_200_one_409(engine: Engine, merchant: str) -> None:
    check_id = held_drop(engine, merchant)
    path = f"/risk-checks/{check_id}/resolution"

    def post(decision: str) -> tuple[str, int]:
        return decision, api(engine).post(path, json=resolution(decision)).status_code

    with ThreadPoolExecutor(2) as pool, engine.connect() as holder:
        holder.begin()
        holder.execute(select(segments).where(segments.c.merchant_id == merchant).with_for_update())
        futures = [pool.submit(post, d) for d in ("approve_lower", "keep_current")]
        raced = wait_for_lock_waiters(engine, UPDATE_SEGMENT, 2)
        holder.rollback()
        results = dict(future.result() for future in futures)

    assert raced, "resolves did not both reach the update"
    assert sorted(results.values()) == [200, 409]
    [winner] = [decision for decision, code in results.items() if code == 200]
    [check] = check_rows(engine, merchant)
    assert (check["status"], check["decision"]) == ("resolved", winner)
    assert kinds(engine, merchant).count("RiskCheckResolved") == 1


def test_a3_approve_writes_resolved_before_changed(engine: Engine, merchant: str) -> None:
    check_id = held_drop(engine, merchant)
    before = len(events(engine, merchant))

    response = api(engine).post(
        f"/risk-checks/{check_id}/resolution", json=resolution("approve_lower")
    )

    assert response.status_code == 200
    written = events(engine, merchant)[before:]
    assert [body["eventType"] for body in written] == ["RiskCheckResolved", "RiskSegmentChanged"]
    assert (written[0]["checkId"], written[0]["decision"]) == (check_id, "approve_lower")
    assert (written[1]["from"], written[1]["to"]) == ("high", "low")


def test_us3_us4_held_drop_then_approved(engine: Engine, merchant: str) -> None:
    client = api(engine)
    high_by_chargebacks(engine, merchant)
    deliver(engine, recorded(merchant, "failed_login", "2026-10-06T00:00"))
    held = client.get(f"/merchants/{merchant}/segment").json()
    deliver(engine, recorded(merchant, "failed_login", "2026-10-12T00:00"))
    lowered = client.get(f"/merchants/{merchant}/segment").json()

    response = client.post(
        f"/risk-checks/{held['openCheck']['checkId']}/resolution",
        json=resolution("approve_lower", "chargebacks settled"),
    )

    assert (held["band"], held["openCheck"]["proposedBand"]) == ("high", "medium")
    assert lowered["openCheck"]["checkId"] == held["openCheck"]["checkId"]
    assert lowered["openCheck"]["proposedBand"] == "low"
    assert response.status_code == 200
    assert response.json()["band"] == "low"
    after = client.get(f"/merchants/{merchant}/segment").json()
    assert (after["band"], after["openCheck"]) == ("low", None)


def test_us4_keep_current_then_new_check(engine: Engine, merchant: str) -> None:
    client = api(engine)
    c1 = held_drop(engine, merchant)

    kept = client.post(f"/risk-checks/{c1}/resolution", json=resolution("keep_current"))
    again = client.post(f"/risk-checks/{c1}/resolution", json=resolution("approve_lower"))
    deliver(engine, recorded(merchant, "failed_login", "2026-10-21T00:00"))

    assert (kept.status_code, kept.json()["band"]) == (200, "high")
    assert again.status_code == 409
    rows = check_rows(engine, merchant)
    assert [(str(r["check_id"]) == c1, r["status"], r["decision"]) for r in rows] == [
        (True, "resolved", "keep_current"),
        (False, "open", None),
    ]
    assert segment_row(engine, merchant)[0] == "high"


def test_us3_rise_supersedes_check(engine: Engine, merchant: str) -> None:
    deliver(engine, recorded(merchant, "chargeback", "2026-09-01T00:00"))
    deliver(engine, recorded(merchant, "failed_login", "2026-10-20T00:00"))
    before = len(events(engine, merchant))

    deliver(engine, recorded(merchant, "kyc_expired", "2026-10-21T00:00"))

    [check] = check_rows(engine, merchant)
    assert (check["status"], check["decision"], check["officer_id"]) == (
        "resolved",
        "superseded",
        None,
    )
    assert segment_row(engine, merchant)[:2] == ("high", "kyc_expired")
    assert kinds(engine, merchant)[before:] == ["RiskCheckResolved", "RiskSegmentChanged"]


def test_43_duplicate_drop_event_one_check(engine: Engine, merchant: str) -> None:
    high_by_chargebacks(engine, merchant)
    drop = recorded(merchant, "failed_login", "2026-10-20T00:00")

    deliver(engine, drop)
    redelivered = deliver(engine, drop)

    assert redelivered.duplicate
    assert len(open_checks(engine, merchant)) == 1
    assert kinds(engine, merchant).count("ManualRiskCheckRequired") == 1


def test_us4_unknown_check_404(engine: Engine) -> None:
    response = api(engine).post(
        "/risk-checks/00000000-0000-0000-0000-000000000009/resolution",
        json=resolution("keep_current"),
    )

    assert response.status_code == 404
