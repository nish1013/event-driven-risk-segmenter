from fakes import FakeAlertLog, Store
from samples import CHECK_ID, MERCHANT, at, changed, deliver, opened, resolved

from notifier.domain.alert import Alert, AlertKind, AlertStatus
from notifier.domain.events import Decision


def only_alert(store: Store) -> Alert:
    assert len(store.alerts) == 1
    return next(iter(store.alerts.values()))


def test_us6_band_change_stores_alert_and_logs() -> None:
    store, log = Store(), FakeAlertLog()

    deliver(store, changed("low", "medium"), log)

    alert = only_alert(store)
    assert (alert.merchant_id, alert.kind, alert.status, alert.check_id) == (
        MERCHANT,
        AlertKind.SEGMENT_CHANGED,
        None,
        None,
    )
    assert alert.details == {"from": "low", "to": "medium", "rule": "rule", "policyVersion": 1}
    assert log.lines == [alert]


def test_us6_i16_duplicate_change_one_alert() -> None:
    store, log = Store(), FakeAlertLog()
    event = changed("low", "medium")

    deliver(store, event, log)
    again = deliver(store, event, log)

    assert (again, len(store.alerts), len(log.lines)) == (None, 1, 1)


def test_i16_two_changes_out_of_order_two_alerts() -> None:
    store = Store()

    deliver(store, changed("medium", "high", "2026-10-05T10:00"))
    deliver(store, changed("low", "medium", "2026-10-01T10:00"))

    details = sorted((a.created_at, a.details["to"]) for a in store.alerts.values())
    assert details == [(at("2026-10-01T10:00"), "medium"), (at("2026-10-05T10:00"), "high")]


def test_us7_check_required_opens_alert() -> None:
    store = Store()

    deliver(store, opened())

    alert = only_alert(store)
    assert (alert.kind, alert.status, alert.check_id) == (
        AlertKind.RISK_CHECK_REQUIRED,
        AlertStatus.OPEN,
        CHECK_ID,
    )
    assert alert.details == {"currentBand": "high", "proposedBand": "low", "policyVersion": 1}


def test_us7_i13_duplicate_check_required_one_alert() -> None:
    store, log = Store(), FakeAlertLog()
    event = opened()

    deliver(store, event, log)
    deliver(store, event, log)

    assert (only_alert(store).status, len(log.lines)) == (AlertStatus.OPEN, 1)


def test_us8_keep_current_closes_alert() -> None:
    store = Store()
    first = deliver(store, opened())

    deliver(store, resolved(Decision.KEEP_CURRENT))

    alert = only_alert(store)
    assert first is not None and alert.alert_id == first.alert_id
    assert (alert.status, alert.decision, alert.closed_at) == (
        AlertStatus.CLOSED,
        Decision.KEEP_CURRENT,
        at("2026-10-21T10:00"),
    )
    assert alert.details["proposedBand"] == "low"
    assert alert.details["officerId"] == "o-7"


def test_us8_superseded_closes_alert() -> None:
    store = Store()
    deliver(store, opened())

    deliver(store, resolved(Decision.SUPERSEDED, officer_id=None))

    alert = only_alert(store)
    assert (alert.status, alert.decision) == (AlertStatus.CLOSED, Decision.SUPERSEDED)


def test_us8_i14_replayed_opened_after_close_stays_closed() -> None:
    store, log = Store(), FakeAlertLog()
    event = opened()
    deliver(store, resolved(Decision.KEEP_CURRENT), log)

    replayed = deliver(store, event, log)

    assert replayed is None
    assert (only_alert(store).status, only_alert(store).decision) == (
        AlertStatus.CLOSED,
        Decision.KEEP_CURRENT,
    )
    assert len(log.lines) == 1


def test_43_resolved_before_opened_ends_closed() -> None:
    store = Store()

    deliver(store, resolved(Decision.APPROVE_LOWER))
    deliver(store, opened())

    alert = only_alert(store)
    assert (alert.status, alert.decision) == (AlertStatus.CLOSED, Decision.APPROVE_LOWER)


def test_i14_second_resolution_keeps_first_decision() -> None:
    store = Store()
    deliver(store, opened())
    deliver(store, resolved(Decision.KEEP_CURRENT))

    deliver(store, resolved(Decision.SUPERSEDED, officer_id=None))

    assert only_alert(store).decision is Decision.KEEP_CURRENT


def test_43_duplicate_resolved_applied_once() -> None:
    store, log = Store(), FakeAlertLog()
    event = resolved(Decision.KEEP_CURRENT)
    deliver(store, opened(), log)

    deliver(store, event, log)
    deliver(store, event, log)

    assert (only_alert(store).status, len(log.lines)) == (AlertStatus.CLOSED, 2)


def test_12_log_failure_keeps_alert() -> None:
    store = Store()

    stored = deliver(store, changed("low", "medium"), FakeAlertLog(fail=True))

    assert stored is not None and only_alert(store) == stored
