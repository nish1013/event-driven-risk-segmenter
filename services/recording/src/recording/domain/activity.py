from dataclasses import dataclass, field
from datetime import datetime, timedelta
from uuid import UUID

from recording.domain.errors import OccurredAtInFuture
from recording.domain.events import ActivityRecorded
from recording.domain.schemas import AnyNewActivity

MAX_CLOCK_SKEW = timedelta(minutes=5)


@dataclass
class Activity:
    activity_id: str
    merchant_id: str
    type: str
    occurred_at: datetime
    payload: dict[str, str]
    recorded_at: datetime
    events: list[ActivityRecorded] = field(default_factory=list, compare=False, repr=False)

    @classmethod
    def record(cls, new: AnyNewActivity, recorded_at: datetime, event_id: UUID) -> Activity:
        if new.occurred_at > recorded_at + MAX_CLOCK_SKEW:
            raise OccurredAtInFuture(f"occurredAt is more than {MAX_CLOCK_SKEW} ahead")
        activity = cls(
            activity_id=new.activity_id,
            merchant_id=new.merchant_id,
            type=new.type,
            occurred_at=new.occurred_at,
            payload=new.payload.model_dump(by_alias=True),
            recorded_at=recorded_at,
        )
        activity.events.append(
            ActivityRecorded(
                event_id=event_id,
                activity_id=activity.activity_id,
                merchant_id=activity.merchant_id,
                type=activity.type,
                occurred_at=activity.occurred_at,
                payload=activity.payload,
                recorded_at=recorded_at,
            )
        )
        return activity

    def same_body(self, other: Activity) -> bool:
        return (self.merchant_id, self.type, self.occurred_at, self.payload) == (
            other.merchant_id,
            other.type,
            other.occurred_at,
            other.payload,
        )
