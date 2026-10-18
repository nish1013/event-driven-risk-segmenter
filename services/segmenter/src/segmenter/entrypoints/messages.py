from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, StringConstraints
from pydantic.alias_generators import to_camel

from segmenter.domain.segment import Fact
from segmenter.service_layer.on_activity_recorded import ActivityRecorded

NonEmpty = Annotated[str, StringConstraints(min_length=1)]


class ActivityRecordedMessage(BaseModel):
    # Unknown fields are ignored: the event contract grows by addition only (#5).
    model_config = ConfigDict(extra="ignore", frozen=True, alias_generator=to_camel)

    event_id: UUID
    activity_id: NonEmpty
    merchant_id: NonEmpty
    type: Literal["chargeback", "velocity_breach", "kyc_expired", "failed_login"]
    occurred_at: AwareDatetime

    def to_event(self) -> ActivityRecorded:
        return ActivityRecorded(
            event_id=self.event_id,
            fact=Fact(self.activity_id, self.merchant_id, self.type, self.occurred_at),
        )
