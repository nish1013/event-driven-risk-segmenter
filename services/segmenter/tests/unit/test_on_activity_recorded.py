import pytest
from fakes import Store
from samples import deliver, recorded

from segmenter.domain.errors import VersionConflict
from segmenter.domain.events import RiskSegmentChanged
from segmenter.domain.policy import Band


def band(store: Store, merchant_id: str = "m-100") -> tuple[Band, str | None]:
    row = store.segments[merchant_id]
    return row.band, row.rule


def changes(store: Store) -> list[tuple[str, str]]:
    return [
        (event.from_band.value, event.to_band.value)
        for event in store.outbox
        if isinstance(event, RiskSegmentChanged)
    ]


def test_us2_first_chargeback_gives_medium() -> None:
    store = Store()

    deliver(store, recorded("chargeback", "2026-10-01T00:00"))

    assert band(store) == (Band.MEDIUM, "chargeback_1_in_30d")
    assert changes(store) == [("low", "medium")]
    assert store.outbox[0].policy_version == 1


def test_us2_third_chargeback_in_30d_gives_high() -> None:
    store = Store()
    deliver(store, recorded("chargeback", "2026-10-01T00:00"))
    deliver(store, recorded("chargeback", "2026-10-05T00:00"))

    deliver(store, recorded("chargeback", "2026-10-20T00:00"))

    assert band(store) == (Band.HIGH, "chargeback_3_in_30d")
    assert changes(store) == [("low", "medium"), ("medium", "high")]


def test_us2_second_chargeback_no_event() -> None:
    store = Store()
    deliver(store, recorded("chargeback", "2026-10-01T00:00"))

    deliver(store, recorded("chargeback", "2026-10-05T00:00"))

    assert band(store) == (Band.MEDIUM, "chargeback_1_in_30d")
    assert changes(store) == [("low", "medium")]


def test_us2_i5_unknown_merchant_stored_low_no_event() -> None:
    store = Store()

    deliver(store, recorded("failed_login", "2026-10-01T09:00", merchant_id="m-200"))

    assert band(store, "m-200") == (Band.LOW, None)
    assert store.outbox == []


def test_us2_tenth_failed_login_in_1h_gives_medium() -> None:
    store = Store()
    for minute in range(0, 45, 5):
        deliver(store, recorded("failed_login", f"2026-10-01T09:{minute:02d}", "m-200"))
    nine = band(store, "m-200")

    deliver(store, recorded("failed_login", "2026-10-01T09:50", "m-200"))

    assert nine == (Band.LOW, None)
    assert band(store, "m-200") == (Band.MEDIUM, "failed_login_10_in_1h")


def test_us2_kyc_expired_beats_chargeback() -> None:
    store = Store()
    deliver(store, recorded("chargeback", "2026-10-01T00:00"))

    deliver(store, recorded("kyc_expired", "2026-10-06T00:00"))

    assert band(store) == (Band.HIGH, "kyc_expired")


def test_us2_i10_duplicate_delivery_counted_once() -> None:
    store = Store()
    event = recorded("chargeback", "2026-10-01T00:00")

    first = deliver(store, event)
    second = deliver(store, event)

    assert (first.duplicate, second.duplicate) == (False, True)
    assert len(store.facts) == 1
    assert band(store) == (Band.MEDIUM, "chargeback_1_in_30d")
    assert changes(store) == [("low", "medium")]
    assert store.segments["m-100"].version_number == 1


def test_us2_window_excludes_fact_1s_too_old() -> None:
    store = Store()
    deliver(store, recorded("chargeback", "2026-09-01T10:00:00"))
    deliver(store, recorded("chargeback", "2026-10-01T10:00:00"))

    deliver(store, recorded("chargeback", "2026-10-01T10:00:01"))

    assert band(store) == (Band.MEDIUM, "chargeback_1_in_30d")


def test_43_out_of_order_gives_same_band_and_changes() -> None:
    in_order, reversed_ = Store(), Store()
    events = [
        recorded("chargeback", day, activity_id=f"a-cb-{day}")
        for day in ("2026-10-01T00:00", "2026-10-05T00:00", "2026-10-20T00:00")
    ]

    for event in events:
        deliver(in_order, event)
    for event in reversed(events):
        deliver(reversed_, event)

    assert band(in_order) == band(reversed_) == (Band.HIGH, "chargeback_3_in_30d")
    assert changes(in_order) == changes(reversed_) == [("low", "medium"), ("medium", "high")]


def test_43_late_fact_outside_window_ignored_and_pruned() -> None:
    store = Store()
    deliver(store, recorded("failed_login", "2026-10-20T00:00"))

    deliver(store, recorded("chargeback", "2026-09-01T00:00"))

    assert band(store) == (Band.LOW, None)
    assert [fact.type for fact in store.facts.values()] == ["failed_login"]


def test_43_duplicate_after_later_event_changes_nothing() -> None:
    store = Store()
    first = recorded("chargeback", "2026-10-01T00:00")
    deliver(store, first)
    deliver(store, recorded("chargeback", "2026-10-05T00:00"))

    deliver(store, first)

    assert len(store.facts) == 2
    assert changes(store) == [("low", "medium")]


def test_a4_one_conflict_applied_on_rerun() -> None:
    store = Store()

    applied = deliver(store, recorded("chargeback", "2026-10-01T00:00"), conflicts=[1])

    assert applied.runs == 2
    assert band(store) == (Band.MEDIUM, "chargeback_1_in_30d")
    assert changes(store) == [("low", "medium")]
    assert len(store.facts) == 1


def test_a4_three_conflicts_raise_for_nack() -> None:
    store = Store()

    with pytest.raises(VersionConflict):
        deliver(store, recorded("chargeback", "2026-10-01T00:00"), conflicts=[3])

    assert store.segments == {}
    assert store.facts == {}
    assert store.processed == set()
    assert store.outbox == []


def test_p6_kyc_expired_never_pruned() -> None:
    store = Store()
    deliver(store, recorded("kyc_expired", "2020-01-01T00:00"))

    deliver(store, recorded("failed_login", "2026-10-20T00:00"))

    assert sorted(fact.type for fact in store.facts.values()) == ["failed_login", "kyc_expired"]
    assert band(store) == (Band.HIGH, "kyc_expired")
