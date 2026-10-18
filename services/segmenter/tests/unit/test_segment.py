from datetime import UTC, datetime
from itertools import count
from uuid import UUID

import pytest

from segmenter.domain.check import Check, CheckStatus, Decision
from segmenter.domain.errors import CheckNotOpen, InvariantViolation
from segmenter.domain.events import (
    ManualRiskCheckRequired,
    RiskCheckResolved,
    RiskSegmentChanged,
)
from segmenter.domain.policy import POLICY_V1, Band
from segmenter.domain.segment import FactCounts, Segment

NOW = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)
LOADED_AT = datetime(2026, 10, 1, tzinfo=UTC)
C1 = UUID(int=900)


def segment(band: Band, check: Band | None = None, **fired: int) -> Segment:
    checks = [] if check is None else [Check.open(C1, "m-100", check, LOADED_AT)]
    return Segment(
        merchant_id="m-100",
        band=band,
        rule=None,
        policy_version=1,
        version_number=7,
        updated_at=LOADED_AT,
        fact_counts=FactCounts(LOADED_AT, {rule.name: 0 for rule in POLICY_V1.rules} | fired),
        checks=checks,
    )


def kinds(root: Segment) -> list[str]:
    return [type(event).__name__ for event in root.events]


def apply(root: Segment) -> None:
    numbers = count(1)
    root.apply(POLICY_V1, NOW, lambda: UUID(int=next(numbers)))


def resolve(root: Segment, decision: Decision, check_id: UUID = C1) -> Check:
    numbers = count(1)
    return root.resolve(
        check_id, decision, "o-7", "reason", POLICY_V1, NOW, lambda: UUID(int=next(numbers))
    )


def test_p4_higher_no_check_raises_band() -> None:
    root = segment(Band.MEDIUM, chargeback_3_in_30d=3, chargeback_1_in_30d=3)

    apply(root)

    assert (root.band, root.rule, root.updated_at) == (Band.HIGH, "chargeback_3_in_30d", NOW)
    assert root.events == [
        RiskSegmentChanged(
            UUID(int=1), "m-100", NOW, Band.MEDIUM, Band.HIGH, "chargeback_3_in_30d", 1
        )
    ]


def test_p4_us3_higher_with_check_supersedes_it() -> None:
    root = segment(Band.MEDIUM, check=Band.LOW, kyc_expired=1)

    apply(root)

    assert root.band == Band.HIGH
    assert root.open_check is None
    assert (root.checks[0].status, root.checks[0].decision) == (
        CheckStatus.RESOLVED,
        Decision.SUPERSEDED,
    )
    assert kinds(root) == ["RiskCheckResolved", "RiskSegmentChanged"]
    resolved = root.events[0]
    assert isinstance(resolved, RiskCheckResolved)
    assert (resolved.check_id, resolved.officer_id, resolved.reason) == (C1, None, None)


def test_p4_same_band_changes_nothing() -> None:
    root = segment(Band.MEDIUM, chargeback_1_in_30d=2)

    apply(root)

    assert (root.band, root.rule, root.events, root.checks) == (Band.MEDIUM, None, [], [])


def test_d4_same_band_with_check_supersedes_it() -> None:
    root = segment(Band.HIGH, check=Band.MEDIUM, kyc_expired=1)

    apply(root)

    assert root.band == Band.HIGH
    assert root.open_check is None
    assert root.checks[0].decision == Decision.SUPERSEDED
    assert kinds(root) == ["RiskCheckResolved"]


def test_p4_us3_lower_no_check_opens_one() -> None:
    root = segment(Band.HIGH)

    apply(root)

    assert root.band == Band.HIGH
    check = root.open_check
    assert check is not None
    assert (check.proposed_band, check.opened_at) == (Band.LOW, NOW)
    assert root.events == [
        ManualRiskCheckRequired(UUID(int=2), "m-100", NOW, check.check_id, Band.HIGH, Band.LOW, 1)
    ]


def test_p4_us3_lower_with_check_updates_proposal() -> None:
    root = segment(Band.HIGH, check=Band.MEDIUM)

    apply(root)

    assert root.band == Band.HIGH
    assert [(c.check_id, c.proposed_band) for c in root.checks] == [(C1, Band.LOW)]
    assert root.events == []


def test_p5_us4_approve_lower_sets_proposal() -> None:
    root = segment(Band.HIGH, check=Band.LOW)

    check = resolve(root, Decision.APPROVE_LOWER)

    assert (root.band, root.rule, root.updated_at) == (Band.LOW, None, NOW)
    assert (check.status, check.decision, check.officer_id, check.reason) == (
        CheckStatus.RESOLVED,
        Decision.APPROVE_LOWER,
        "o-7",
        "reason",
    )
    assert kinds(root) == ["RiskCheckResolved", "RiskSegmentChanged"]


def test_p5_approve_records_rule_of_proposal() -> None:
    root = segment(Band.HIGH, check=Band.MEDIUM, chargeback_1_in_30d=1)

    resolve(root, Decision.APPROVE_LOWER)

    assert (root.band, root.rule) == (Band.MEDIUM, "chargeback_1_in_30d")


def test_p5_us4_keep_current_resolves_only() -> None:
    root = segment(Band.HIGH, check=Band.LOW)

    resolve(root, Decision.KEEP_CURRENT)

    assert (root.band, root.updated_at) == (Band.HIGH, LOADED_AT)
    assert root.open_check is None
    assert kinds(root) == ["RiskCheckResolved"]


def test_us4_unknown_or_closed_check_not_open() -> None:
    root = segment(Band.HIGH, check=Band.LOW)
    resolve(root, Decision.KEEP_CURRENT)

    with pytest.raises(CheckNotOpen):
        resolve(root, Decision.APPROVE_LOWER)
    with pytest.raises(CheckNotOpen):
        resolve(segment(Band.HIGH, check=Band.LOW), Decision.KEEP_CURRENT, UUID(int=1))


def test_p5_officer_cannot_supersede() -> None:
    with pytest.raises(ValueError, match="superseded"):
        resolve(segment(Band.HIGH, check=Band.LOW), Decision.SUPERSEDED)


def test_i8_check_not_below_band_refused() -> None:
    root = segment(Band.MEDIUM, check=Band.MEDIUM)

    with pytest.raises(InvariantViolation, match="I8"):
        root.assert_invariants()


def test_i7_two_open_checks_refused() -> None:
    root = segment(Band.HIGH, check=Band.LOW)
    root.checks.append(Check.open(UUID(int=901), "m-100", Band.MEDIUM, NOW))

    with pytest.raises(InvariantViolation, match="I7"):
        root.assert_invariants()


def test_i11_event_bodies() -> None:
    root = segment(Band.MEDIUM, check=Band.LOW, kyc_expired=1)

    apply(root)

    assert [event.body() for event in root.events] == [
        {
            "eventId": str(UUID(int=1)),
            "eventType": "RiskCheckResolved",
            "merchantId": "m-100",
            "occurredAt": NOW.isoformat(),
            "checkId": str(C1),
            "decision": "superseded",
            "officerId": None,
            "reason": None,
        },
        {
            "eventId": str(UUID(int=2)),
            "eventType": "RiskSegmentChanged",
            "merchantId": "m-100",
            "occurredAt": NOW.isoformat(),
            "from": "medium",
            "to": "high",
            "rule": "kyc_expired",
            "policyVersion": 1,
        },
    ]


def test_i11_check_required_body() -> None:
    root = segment(Band.HIGH)

    apply(root)

    check = root.open_check
    assert check is not None
    assert [event.body() for event in root.events] == [
        {
            "eventId": str(UUID(int=2)),
            "eventType": "ManualRiskCheckRequired",
            "merchantId": "m-100",
            "occurredAt": NOW.isoformat(),
            "checkId": str(check.check_id),
            "currentBand": "high",
            "proposedBand": "low",
            "policyVersion": 1,
        }
    ]
