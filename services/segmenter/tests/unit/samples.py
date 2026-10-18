from datetime import UTC, datetime
from itertools import count
from uuid import UUID

from fakes import FakeUnitOfWork, Store

from segmenter.domain.policy import POLICY_V1
from segmenter.domain.segment import Fact
from segmenter.service_layer.on_activity_recorded import (
    ActivityRecorded,
    Applied,
    on_activity_recorded,
)

NOW = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)
_ids = count(1)


def at(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=UTC)


def recorded(
    fact_type: str, occurred_at: str, merchant_id: str = "m-100", activity_id: str | None = None
) -> ActivityRecorded:
    n = next(_ids)
    return ActivityRecorded(
        event_id=UUID(int=n),
        fact=Fact(activity_id or f"a-{n}", merchant_id, fact_type, at(occurred_at)),
    )


def deliver(store: Store, event: ActivityRecorded, conflicts: list[int] | None = None) -> Applied:
    return on_activity_recorded(
        event,
        lambda: FakeUnitOfWork(store, conflicts),
        POLICY_V1,
        lambda: NOW,
        lambda: UUID(int=10_000 + next(_ids)),
        lambda _: None,
    )
