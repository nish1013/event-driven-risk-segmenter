import time
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import Engine, func, select, text

from segmenter.adapters.tables import outbox, segments
from segmenter.domain.policy import POLICY_V1
from segmenter.domain.segment import Fact
from segmenter.service_layer.on_activity_recorded import (
    ActivityRecorded,
    Applied,
    on_activity_recorded,
)
from segmenter.service_layer.unit_of_work import SqlUnitOfWork


def at(text_value: str) -> datetime:
    return datetime.fromisoformat(text_value).replace(tzinfo=UTC)


def recorded(merchant_id: str, fact_type: str, occurred_at: str) -> ActivityRecorded:
    return ActivityRecorded(
        event_id=uuid4(),
        fact=Fact(f"a-{uuid4()}", merchant_id, fact_type, at(occurred_at)),
    )


def deliver(engine: Engine, event: ActivityRecorded) -> Applied:
    return on_activity_recorded(
        event,
        lambda: SqlUnitOfWork(engine),
        POLICY_V1,
        lambda: datetime.now(UTC),
        uuid4,
        time.sleep,
    )


def segment_row(engine: Engine, merchant_id: str) -> tuple[str, str | None, int]:
    with engine.connect() as connection:
        row = connection.execute(
            select(segments.c.band, segments.c.rule, segments.c.version_number).where(
                segments.c.merchant_id == merchant_id
            )
        ).one()
    return row.band, row.rule, row.version_number


def changes(engine: Engine, merchant_id: str) -> list[tuple[str, str]]:
    with engine.connect() as connection:
        bodies = connection.scalars(
            select(outbox.c.body).where(outbox.c.ordering_key == merchant_id).order_by(outbox.c.id)
        )
        return [(body["from"], body["to"]) for body in bodies]


def segment_count(engine: Engine, merchant_id: str) -> int:
    with engine.connect() as connection:
        return (
            connection.scalar(select(func.count()).where(segments.c.merchant_id == merchant_id))
            or 0
        )


def wait_for_lock_waiters(engine: Engine, statement_prefix: str, expected: int) -> bool:
    query = text(
        "SELECT count(*) FROM pg_stat_activity "
        "WHERE usename = current_user AND wait_event_type = 'Lock' AND query LIKE :prefix"
    )
    deadline = time.monotonic() + 10
    with engine.connect() as connection:
        while True:
            waiting = connection.scalar(query, {"prefix": f"{statement_prefix}%"}) or 0
            # pg_stat_activity is a snapshot per transaction; end it so each poll reads fresh.
            connection.rollback()
            if waiting >= expected:
                return True
            if time.monotonic() > deadline:
                return False
            time.sleep(0.02)
