from uuid import UUID

import pytest
from fakes import Store
from samples import deliver, recorded, resolve

from segmenter.domain.check import Check, Decision
from segmenter.domain.errors import VersionConflict
from segmenter.domain.policy import Band
from segmenter.service_layer.resolve_risk_check import Outcome


def kinds(store: Store, start: int = 0) -> list[str]:
    return [str(event.body()["eventType"]) for event in store.outbox[start:]]


def open_checks(store: Store, merchant_id: str = "m-100") -> list[Check]:
    return [c for c in store.checks.values() if c.merchant_id == merchant_id and c.is_open]


def the_open_check(store: Store, merchant_id: str = "m-100") -> Check:
    [check] = open_checks(store, merchant_id)
    return check


def high_by_chargebacks(store: Store, merchant_id: str = "m-100") -> None:
    for day in ("2026-09-01", "2026-09-05", "2026-09-10"):
        deliver(store, recorded("chargeback", f"{day}T00:00", merchant_id))


def held_drop(store: Store) -> Check:
    high_by_chargebacks(store)
    deliver(store, recorded("failed_login", "2026-10-20T00:00"))
    return the_open_check(store)


def test_us3_drop_held_and_check_opened() -> None:
    store = Store()
    high_by_chargebacks(store)
    before = len(store.outbox)

    deliver(store, recorded("failed_login", "2026-10-20T00:00"))

    assert store.segments["m-100"].band == Band.HIGH
    assert the_open_check(store).proposed_band == Band.LOW
    assert kinds(store, before) == ["ManualRiskCheckRequired"]


def test_us3_lower_again_updates_proposal_silently() -> None:
    store = Store()
    high_by_chargebacks(store, "m-300")
    deliver(store, recorded("failed_login", "2026-10-06T00:00", "m-300"))
    c1 = the_open_check(store, "m-300")
    before = len(store.outbox)

    deliver(store, recorded("failed_login", "2026-10-12T00:00", "m-300"))

    assert c1.proposed_band == Band.MEDIUM
    assert the_open_check(store, "m-300").check_id == c1.check_id
    assert the_open_check(store, "m-300").proposed_band == Band.LOW
    assert store.segments["m-300"].band == Band.HIGH
    assert kinds(store, before) == []


def test_us3_rise_supersedes_open_check() -> None:
    store = Store()
    deliver(store, recorded("chargeback", "2026-09-01T00:00", "m-400"))
    deliver(store, recorded("failed_login", "2026-10-20T00:00", "m-400"))
    c2 = the_open_check(store, "m-400")
    before = len(store.outbox)

    deliver(store, recorded("kyc_expired", "2026-10-21T00:00", "m-400"))

    assert store.segments["m-400"].band == Band.HIGH
    assert store.checks[c2.check_id].decision == Decision.SUPERSEDED
    assert open_checks(store, "m-400") == []
    assert kinds(store, before) == ["RiskCheckResolved", "RiskSegmentChanged"]


def test_us4_approve_lower_sets_band() -> None:
    store = Store()
    c1 = held_drop(store)
    before = len(store.outbox)

    resolution = resolve(store, c1.check_id, Decision.APPROVE_LOWER, "chargebacks settled")

    assert (resolution.outcome, resolution.band) == (Outcome.RESOLVED, Band.LOW)
    assert store.segments["m-100"].band == Band.LOW
    stored = store.checks[c1.check_id]
    assert (stored.decision, stored.officer_id, stored.reason) == (
        Decision.APPROVE_LOWER,
        "o-7",
        "chargebacks settled",
    )
    assert kinds(store, before) == ["RiskCheckResolved", "RiskSegmentChanged"]


def test_us4_keep_current_resolves_only() -> None:
    store = Store()
    c1 = held_drop(store)
    before = len(store.outbox)

    resolve(store, c1.check_id, Decision.KEEP_CURRENT, "pattern still active")

    assert store.segments["m-100"].band == Band.HIGH
    assert store.checks[c1.check_id].decision == Decision.KEEP_CURRENT
    assert kinds(store, before) == ["RiskCheckResolved"]


def test_us4_after_keep_current_next_drop_opens_new() -> None:
    store = Store()
    c1 = held_drop(store)
    resolve(store, c1.check_id, Decision.KEEP_CURRENT)
    before = len(store.outbox)

    deliver(store, recorded("failed_login", "2026-10-21T00:00"))

    c3 = the_open_check(store)
    assert c3.check_id != c1.check_id
    assert kinds(store, before) == ["ManualRiskCheckRequired"]


def test_us4_i9_resolve_twice_409_nothing_changes() -> None:
    store = Store()
    c1 = held_drop(store)
    resolve(store, c1.check_id, Decision.KEEP_CURRENT)
    resolved, outbox = store.checks[c1.check_id], len(store.outbox)

    again = resolve(store, c1.check_id, Decision.APPROVE_LOWER)

    assert again.outcome == Outcome.NOT_OPEN
    assert store.checks[c1.check_id] == resolved
    assert store.segments["m-100"].band == Band.HIGH
    assert len(store.outbox) == outbox


def test_us4_unknown_check_not_found() -> None:
    assert resolve(Store(), UUID(int=9), Decision.KEEP_CURRENT).outcome == Outcome.NOT_FOUND


def test_i9_recompute_leaves_resolved_check_alone() -> None:
    store = Store()
    c1 = held_drop(store)
    resolve(store, c1.check_id, Decision.KEEP_CURRENT)
    resolved = store.checks[c1.check_id]

    deliver(store, recorded("kyc_expired", "2026-10-21T00:00"))

    assert store.checks[c1.check_id] == resolved


def test_a4_resolve_conflict_applied_on_rerun() -> None:
    store = Store()
    c1 = held_drop(store)

    resolution = resolve(store, c1.check_id, Decision.APPROVE_LOWER, conflicts=[1])

    assert (resolution.outcome, resolution.runs) == (Outcome.RESOLVED, 2)
    assert kinds(store).count("RiskCheckResolved") == 1


def test_a4_resolve_three_conflicts_raise_for_503() -> None:
    store = Store()
    c1 = held_drop(store)

    with pytest.raises(VersionConflict):
        resolve(store, c1.check_id, Decision.APPROVE_LOWER, conflicts=[3])

    assert store.checks[c1.check_id].is_open


def test_43_duplicate_drop_event_one_check() -> None:
    store = Store()
    high_by_chargebacks(store)
    drop = recorded("failed_login", "2026-10-20T00:00")

    deliver(store, drop)
    deliver(store, drop)

    assert len(open_checks(store)) == 1
    assert kinds(store).count("ManualRiskCheckRequired") == 1


def test_43_late_older_fact_keeps_one_check() -> None:
    store = Store()
    c1 = held_drop(store)
    before = len(store.outbox)

    deliver(store, recorded("failed_login", "2026-10-19T00:00"))
    deliver(store, recorded("chargeback", "2026-09-02T00:00"))

    assert open_checks(store) == [c1]
    assert kinds(store, before) == []


def test_43_late_fact_in_window_raises_proposal() -> None:
    store = Store()
    c1 = held_drop(store)

    deliver(store, recorded("chargeback", "2026-10-15T00:00"))

    assert store.segments["m-100"].band == Band.HIGH
    assert the_open_check(store).check_id == c1.check_id
    assert the_open_check(store).proposed_band == Band.MEDIUM


def test_43_duplicate_after_resolve_reopens_nothing() -> None:
    store = Store()
    high_by_chargebacks(store)
    drop = recorded("failed_login", "2026-10-20T00:00")
    deliver(store, drop)
    resolve(store, the_open_check(store).check_id, Decision.KEEP_CURRENT)

    deliver(store, drop)

    assert open_checks(store) == []


def test_policy_example_run() -> None:
    store = Store()
    steps = [
        ("chargeback", "2026-09-01T00:00", Band.MEDIUM, None, ["RiskSegmentChanged"]),
        ("chargeback", "2026-09-05T00:00", Band.MEDIUM, None, []),
        ("chargeback", "2026-09-10T00:00", Band.HIGH, None, ["RiskSegmentChanged"]),
        ("failed_login", "2026-10-06T00:00", Band.HIGH, Band.MEDIUM, ["ManualRiskCheckRequired"]),
        ("failed_login", "2026-10-12T00:00", Band.HIGH, Band.LOW, []),
    ]

    for fact_type, occurred_at, band, proposal, events in steps:
        before = len(store.outbox)
        deliver(store, recorded(fact_type, occurred_at))
        checks = open_checks(store)
        assert store.segments["m-100"].band == band, occurred_at
        assert [c.proposed_band for c in checks] == ([] if proposal is None else [proposal])
        assert kinds(store, before) == events, occurred_at
    before = len(store.outbox)

    resolve(store, the_open_check(store).check_id, Decision.APPROVE_LOWER)

    assert store.segments["m-100"].band == Band.LOW
    assert open_checks(store) == []
    assert kinds(store, before) == ["RiskCheckResolved", "RiskSegmentChanged"]
