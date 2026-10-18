from datetime import UTC, datetime
from itertools import count
from uuid import UUID

from fakes import FakeAlertLog, FakeUnitOfWork, Store

from notifier.domain.alert import Alert
from notifier.domain.events import (
    Decision,
    ManualRiskCheckRequired,
    RiskCheckResolved,
    RiskSegmentChanged,
    SegmenterEvent,
)
from notifier.service_layer.on_segmenter_event import on_segmenter_event

MERCHANT = "m-200"
CHECK_ID = UUID(int=1)
NOW = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)
_ids = count(100)


def at(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=UTC)


def changed(
    from_band: str, to_band: str, occurred_at: str = "2026-10-01T10:00"
) -> RiskSegmentChanged:
    return RiskSegmentChanged(
        UUID(int=next(_ids)), MERCHANT, at(occurred_at), from_band, to_band, "rule", 1
    )


def opened(
    check_id: UUID = CHECK_ID, occurred_at: str = "2026-10-20T10:00"
) -> ManualRiskCheckRequired:
    return ManualRiskCheckRequired(
        UUID(int=next(_ids)), MERCHANT, at(occurred_at), check_id, "high", "low", 1
    )


def resolved(
    decision: Decision,
    check_id: UUID = CHECK_ID,
    officer_id: str | None = "o-7",
    occurred_at: str = "2026-10-21T10:00",
) -> RiskCheckResolved:
    reason = None if officer_id is None else "reviewed"
    return RiskCheckResolved(
        UUID(int=next(_ids)), MERCHANT, at(occurred_at), check_id, decision, officer_id, reason
    )


def deliver(store: Store, event: SegmenterEvent, log: FakeAlertLog | None = None) -> Alert | None:
    return on_segmenter_event(
        event,
        lambda: FakeUnitOfWork(store),
        log if log is not None else FakeAlertLog(),
        lambda: NOW,
        lambda: UUID(int=10_000 + next(_ids)),
    )
