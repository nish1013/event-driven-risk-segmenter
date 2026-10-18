from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True)
class ActivityRecorded:
    event_id: UUID
    activity_id: str
    merchant_id: str
    type: str
    occurred_at: datetime
    payload: dict[str, str]
    recorded_at: datetime

    def body(self) -> dict[str, object]:
        return {
            "eventId": str(self.event_id),
            "eventType": "ActivityRecorded",
            "activityId": self.activity_id,
            "merchantId": self.merchant_id,
            "type": self.type,
            "occurredAt": self.occurred_at.isoformat(),
            "payload": self.payload,
            "recordedAt": self.recorded_at.isoformat(),
        }
