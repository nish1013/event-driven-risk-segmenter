from concurrent.futures import Future, ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

from segmenter_db import (
    at,
    changes,
    deliver,
    recorded,
    segment_count,
    segment_row,
    wait_for_lock_waiters,
)
from sqlalchemy import Engine, select, update

from segmenter.adapters.tables import facts, processed_events, segments
from segmenter.service_layer.on_activity_recorded import ActivityRecorded, Applied

UPDATE_SEGMENT = "UPDATE segmenter.segments"


def fact_ids(engine: Engine, merchant_id: str) -> set[str]:
    with engine.connect() as connection:
        return set(
            connection.scalars(
                select(facts.c.activity_id).where(facts.c.merchant_id == merchant_id)
            )
        )


def test_i6_concurrent_events_give_rule_band_of_both(engine: Engine, merchant: str) -> None:
    deliver(engine, recorded(merchant, "chargeback", "2026-10-01T00:00"))
    deliver(engine, recorded(merchant, "chargeback", "2026-10-05T00:00"))
    third = recorded(merchant, "chargeback", "2026-10-20T00:00")
    login = recorded(merchant, "failed_login", "2026-10-20T00:00")

    # Hold the segment row so both workers read the same version before either writes. The
    # chargeback queues first, so the failed login, computed without it, writes last.
    with ThreadPoolExecutor(2) as pool, engine.connect() as holder:
        holder.begin()
        holder.execute(select(segments).where(segments.c.merchant_id == merchant).with_for_update())
        a = pool.submit(deliver, engine, third)
        a_waiting = wait_for_lock_waiters(engine, UPDATE_SEGMENT, 1)
        b = pool.submit(deliver, engine, login)
        both_waiting = wait_for_lock_waiters(engine, UPDATE_SEGMENT, 2)
        holder.rollback()
        runs = (a.result().runs, b.result().runs)

    assert a_waiting and both_waiting, "workers did not both reach the update"
    assert segment_row(engine, merchant)[:2] == ("high", "chargeback_3_in_30d")
    assert runs == (1, 2)
    assert changes(engine, merchant) == [("low", "medium"), ("medium", "high")]


def test_i5_concurrent_first_events_one_segment(engine: Engine, merchant: str) -> None:
    first = recorded(merchant, "chargeback", "2026-10-01T00:00")
    second = recorded(merchant, "kyc_expired", "2026-10-02T00:00")

    with ThreadPoolExecutor(2) as pool, engine.connect() as holder:
        holder.begin()
        holder.execute(
            segments.insert().values(
                merchant_id=merchant,
                band="low",
                policy_version=1,
                version_number=0,
                updated_at=datetime.now(UTC),
            )
        )
        futures = [pool.submit(deliver, engine, event) for event in (first, second)]
        raced = wait_for_lock_waiters(engine, "INSERT INTO segmenter.segments", 2)
        holder.rollback()
        for future in futures:
            future.result()

    assert raced, "both first events did not reach the segment insert"
    assert segment_count(engine, merchant) == 1
    assert segment_row(engine, merchant) == ("high", "kyc_expired", 2)


def test_i10_redelivery_while_first_runs_applies_once(engine: Engine, merchant: str) -> None:
    event = recorded(merchant, "chargeback", "2026-10-01T00:00")

    with ThreadPoolExecutor(2) as pool, engine.connect() as holder:
        holder.begin()
        holder.execute(
            processed_events.insert().values(
                event_id=event.event_id, processed_at=datetime.now(UTC)
            )
        )
        futures: list[Future[Applied]] = [pool.submit(deliver, engine, event) for _ in range(2)]
        raced = wait_for_lock_waiters(engine, "INSERT INTO segmenter.processed_events", 2)
        holder.rollback()
        duplicates = sorted(future.result().duplicate for future in futures)

    assert raced, "both deliveries did not reach the processed-events insert"
    assert duplicates == [False, True]
    assert len(fact_ids(engine, merchant)) == 1
    assert segment_row(engine, merchant) == ("medium", "chargeback_1_in_30d", 1)
    assert changes(engine, merchant) == [("low", "medium")]


def test_i10_relay_resend_same_event_id_changes_once(engine: Engine, merchant: str) -> None:
    event = recorded(merchant, "chargeback", "2026-10-01T00:00")
    deliver(engine, event)
    deliver(engine, recorded(merchant, "chargeback", "2026-10-05T00:00"))

    resent = deliver(engine, event)

    assert resent.duplicate
    assert segment_row(engine, merchant) == ("medium", "chargeback_1_in_30d", 2)
    assert changes(engine, merchant) == [("low", "medium")]


def test_a4_i11_conflict_rerun_applies_once(engine: Engine, merchant: str) -> None:
    deliver(engine, recorded(merchant, "chargeback", "2026-10-01T00:00"))
    event = recorded(merchant, "kyc_expired", "2026-10-05T00:00")

    # The worker reads version 1 and queues on the row lock; the other writer then commits
    # version 2, so the worker's versioned update matches no row.
    with ThreadPoolExecutor(1) as pool, engine.connect() as other_writer:
        other_writer.begin()
        other_writer.execute(
            select(segments).where(segments.c.merchant_id == merchant).with_for_update()
        )
        future = pool.submit(deliver, engine, event)
        waiting = wait_for_lock_waiters(engine, UPDATE_SEGMENT, 1)
        other_writer.execute(
            update(segments)
            .where(segments.c.merchant_id == merchant)
            .values(version_number=segments.c.version_number + 1)
        )
        other_writer.commit()
        applied = future.result()

    assert waiting, "worker did not reach the update"
    assert applied.runs == 2
    assert segment_row(engine, merchant) == ("high", "kyc_expired", 3)
    assert changes(engine, merchant) == [("low", "medium"), ("medium", "high")]


def test_43_reverse_order_gives_same_band(engine: Engine, merchant: str) -> None:
    days = ["2026-10-20T00:00", "2026-10-05T00:00", "2026-10-01T00:00"]

    for day in days:
        deliver(engine, recorded(merchant, "chargeback", day))

    assert segment_row(engine, merchant)[:2] == ("high", "chargeback_3_in_30d")
    assert changes(engine, merchant) == [("low", "medium"), ("medium", "high")]


def test_i12_prune_keeps_window_and_kyc_expired(engine: Engine, merchant: str) -> None:
    anchor = at("2026-10-20T00:00")

    def event(fact_type: str, age: timedelta) -> ActivityRecorded:
        return recorded(merchant, fact_type, (anchor - age).isoformat()[:19])

    newest = event("failed_login", timedelta(0))
    kept = [
        event("kyc_expired", timedelta(days=3000)),
        event("chargeback", timedelta(days=30)),
        event("velocity_breach", timedelta(days=7)),
        event("failed_login", timedelta(hours=1)),
    ]
    pruned = [
        event("chargeback", timedelta(days=30, seconds=1)),
        event("velocity_breach", timedelta(days=7, seconds=1)),
        event("failed_login", timedelta(hours=1, seconds=1)),
    ]

    for each in [newest, *kept, *pruned]:
        deliver(engine, each)

    assert fact_ids(engine, merchant) == {e.fact.activity_id for e in [newest, *kept]}
    assert segment_row(engine, merchant)[:2] == ("high", "kyc_expired")
