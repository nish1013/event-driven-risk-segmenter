from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

from notifier_db import (
    at,
    changed,
    deliver,
    opened,
    resolved,
    stored_alerts,
    wait_for_lock_waiters,
)
from sqlalchemy import Engine, select

from notifier.adapters.alert_repository import SqlAlertRepository
from notifier.adapters.tables import processed_events
from notifier.domain.alert import Alert, AlertKind, AlertStatus
from notifier.domain.events import Decision, SegmenterEvent

INSERT_ALERT = "INSERT INTO notifier.alerts"


def test_us8_i14_replayed_opened_after_close_stays_closed(engine: Engine, merchant: str) -> None:
    check_id = uuid4()
    dead_lettered = opened(merchant, check_id, "2026-10-20T10:00")
    deliver(engine, resolved(merchant, check_id, Decision.KEEP_CURRENT))

    replayed = deliver(engine, dead_lettered)

    [alert] = stored_alerts(engine, merchant)
    assert replayed is None
    assert (alert.kind, alert.status, alert.decision) == (
        AlertKind.RISK_CHECK_REQUIRED,
        AlertStatus.CLOSED,
        Decision.KEEP_CURRENT,
    )


def test_43_duplicates_out_of_order_one_closed_alert(engine: Engine, merchant: str) -> None:
    check_id = uuid4()
    close = resolved(merchant, check_id, Decision.SUPERSEDED)
    open_ = opened(merchant, check_id, "2026-10-20T10:00")

    for event in (close, open_, close, open_):
        deliver(engine, event)

    assert [(a.status, a.decision) for a in stored_alerts(engine, merchant)] == [
        (AlertStatus.CLOSED, Decision.SUPERSEDED)
    ]


def test_us8_close_keeps_alert_id_and_details(engine: Engine, merchant: str) -> None:
    check_id = uuid4()
    first = deliver(engine, opened(merchant, check_id, "2026-10-20T10:00"))

    deliver(engine, resolved(merchant, check_id, Decision.KEEP_CURRENT))

    [alert] = stored_alerts(engine, merchant)
    assert first is not None and alert.alert_id == first.alert_id
    assert (alert.status, alert.closed_at) == (AlertStatus.CLOSED, at("2026-10-21T10:00"))
    assert (alert.details["proposedBand"], alert.details["officerId"]) == ("low", "o-7")


def test_i10_i16_event_delivered_twice_one_alert(engine: Engine, merchant: str) -> None:
    event = changed(merchant, "medium", "2026-10-01T10:00")

    deliver(engine, event)
    again = deliver(engine, event)

    with engine.connect() as connection:
        seen = connection.scalar(
            select(processed_events.c.event_id).where(processed_events.c.event_id == event.event_id)
        )
    assert (again, len(stored_alerts(engine, merchant)), seen) == (None, 1, event.event_id)


def test_i13_concurrent_opened_then_resolved_one_closed_alert(
    engine: Engine, merchant: str
) -> None:
    check_id = uuid4()
    alert = race(
        engine,
        opened(merchant, check_id, "2026-10-20T10:00"),
        resolved(merchant, check_id, Decision.APPROVE_LOWER),
    )

    assert alert is not None and alert.status is AlertStatus.CLOSED
    assert [a.status for a in stored_alerts(engine, merchant)] == [AlertStatus.CLOSED]


def test_i13_concurrent_resolved_then_opened_one_closed_alert(
    engine: Engine, merchant: str
) -> None:
    check_id = uuid4()
    alert = race(
        engine,
        resolved(merchant, check_id, Decision.APPROVE_LOWER),
        opened(merchant, check_id, "2026-10-20T10:00"),
    )

    assert alert is None
    assert [a.status for a in stored_alerts(engine, merchant)] == [AlertStatus.CLOSED]


def race(engine: Engine, first: SegmenterEvent, second: SegmenterEvent) -> Alert | None:
    # The first insert stays uncommitted, so the second waits on the check_id index and then
    # meets the committed row: the outcome of two workers delivering one check's events at once.
    with ThreadPoolExecutor(1) as pool, engine.connect() as holder:
        holder.begin()
        SqlAlertRepository(holder).record(Alert.of(first, uuid4()))
        later = pool.submit(deliver, engine, second)
        waiting = wait_for_lock_waiters(engine, INSERT_ALERT, 1)
        holder.commit()
        result = later.result()
    assert waiting, "second delivery did not wait on the first"
    return result
