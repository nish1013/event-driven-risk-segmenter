from datetime import UTC, datetime
from uuid import UUID

from segmenter.domain.events import RiskSegmentChanged
from segmenter.domain.policy import POLICY_V1, Band
from segmenter.domain.segment import FactCounts, Segment

EVENT_ID = UUID(int=1)
NOW = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)
LOADED_AT = datetime(2026, 10, 1, tzinfo=UTC)


def segment(band: Band, **fired: int) -> Segment:
    return Segment(
        merchant_id="m-100",
        band=band,
        rule=None,
        policy_version=1,
        version_number=7,
        updated_at=LOADED_AT,
        fact_counts=FactCounts(LOADED_AT, {rule.name: 0 for rule in POLICY_V1.rules} | fired),
    )


def test_p4_higher_raises_band_with_one_event() -> None:
    root = segment(Band.MEDIUM, chargeback_3_in_30d=3, chargeback_1_in_30d=3)

    root.apply(POLICY_V1, NOW, EVENT_ID)

    assert (root.band, root.rule, root.updated_at) == (Band.HIGH, "chargeback_3_in_30d", NOW)
    assert root.events == [
        RiskSegmentChanged(EVENT_ID, "m-100", NOW, Band.MEDIUM, Band.HIGH, "chargeback_3_in_30d", 1)
    ]


def test_p4_same_band_changes_nothing() -> None:
    root = segment(Band.MEDIUM, chargeback_1_in_30d=2)

    root.apply(POLICY_V1, NOW, EVENT_ID)

    assert (root.band, root.rule, root.events) == (Band.MEDIUM, None, [])


def test_i6_lower_rule_band_holds_band() -> None:
    root = segment(Band.HIGH)

    root.apply(POLICY_V1, NOW, EVENT_ID)

    assert (root.band, root.events) == (Band.HIGH, [])


def test_i11_one_event_body_per_change() -> None:
    root = segment(Band.LOW, kyc_expired=1)

    root.apply(POLICY_V1, NOW, EVENT_ID)

    assert [event.body() for event in root.events] == [
        {
            "eventId": str(EVENT_ID),
            "eventType": "RiskSegmentChanged",
            "merchantId": "m-100",
            "occurredAt": NOW.isoformat(),
            "from": "low",
            "to": "high",
            "rule": "kyc_expired",
            "policyVersion": 1,
        }
    ]
