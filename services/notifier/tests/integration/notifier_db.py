import logging
import time
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import Engine, select, text

from notifier.adapters.alert_log import StructuredAlertLog
from notifier.adapters.alert_repository import alert_from_row
from notifier.adapters.tables import alerts
from notifier.domain.alert import Alert
from notifier.domain.events import (
    Decision,
    ManualRiskCheckRequired,
    RiskCheckResolved,
    RiskSegmentChanged,
    SegmenterEvent,
)
from notifier.service_layer.on_segmenter_event import on_segmenter_event
from notifier.service_layer.unit_of_work import SqlUnitOfWork


def at(text_value: str) -> datetime:
    return datetime.fromisoformat(text_value).replace(tzinfo=UTC)


def changed(merchant_id: str, to_band: str, occurred_at: str) -> RiskSegmentChanged:
    return RiskSegmentChanged(uuid4(), merchant_id, at(occurred_at), "low", to_band, "rule", 1)


def opened(merchant_id: str, check_id: UUID, occurred_at: str) -> ManualRiskCheckRequired:
    return ManualRiskCheckRequired(
        uuid4(), merchant_id, at(occurred_at), check_id, "high", "low", 1
    )


def resolved(merchant_id: str, check_id: UUID, decision: Decision) -> RiskCheckResolved:
    return RiskCheckResolved(
        uuid4(), merchant_id, at("2026-10-21T10:00"), check_id, decision, "o-7", "reviewed"
    )


def deliver(engine: Engine, event: SegmenterEvent) -> Alert | None:
    return on_segmenter_event(
        event,
        lambda: SqlUnitOfWork(engine),
        StructuredAlertLog(logging.getLogger("notifier.test.alerts")),
        lambda: datetime.now(UTC),
        uuid4,
    )


def stored_alerts(engine: Engine, merchant_id: str) -> list[Alert]:
    with engine.connect() as connection:
        rows = connection.execute(
            select(alerts).where(alerts.c.merchant_id == merchant_id).order_by(alerts.c.created_at)
        ).mappings()
        return [alert_from_row(row) for row in rows]


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
