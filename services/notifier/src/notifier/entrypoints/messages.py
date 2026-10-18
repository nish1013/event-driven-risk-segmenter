from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints
from pydantic.alias_generators import to_camel

from notifier.domain.events import (
    Decision,
    ManualRiskCheckRequired,
    RiskCheckResolved,
    RiskSegmentChanged,
    SegmenterEvent,
)

NonEmpty = Annotated[str, StringConstraints(min_length=1)]
Band = Literal["low", "medium", "high"]


class Envelope(BaseModel):
    model_config = ConfigDict(extra="ignore", alias_generator=to_camel)

    event_type: str


class EventMessage(BaseModel):
    # Unknown fields are ignored: the event contract grows by addition only (#5).
    model_config = ConfigDict(extra="ignore", frozen=True, alias_generator=to_camel)

    event_id: UUID
    merchant_id: NonEmpty
    occurred_at: AwareDatetime


class RiskSegmentChangedMessage(EventMessage):
    from_band: Band = Field(alias="from")
    to_band: Band = Field(alias="to")
    rule: str | None
    policy_version: int

    def to_event(self) -> RiskSegmentChanged:
        return RiskSegmentChanged(
            self.event_id,
            self.merchant_id,
            self.occurred_at,
            self.from_band,
            self.to_band,
            self.rule,
            self.policy_version,
        )


class ManualRiskCheckRequiredMessage(EventMessage):
    check_id: UUID
    current_band: Band
    proposed_band: Band
    policy_version: int

    def to_event(self) -> ManualRiskCheckRequired:
        return ManualRiskCheckRequired(
            self.event_id,
            self.merchant_id,
            self.occurred_at,
            self.check_id,
            self.current_band,
            self.proposed_band,
            self.policy_version,
        )


class RiskCheckResolvedMessage(EventMessage):
    check_id: UUID
    decision: Decision
    officer_id: str | None
    reason: str | None

    def to_event(self) -> RiskCheckResolved:
        return RiskCheckResolved(
            self.event_id,
            self.merchant_id,
            self.occurred_at,
            self.check_id,
            self.decision,
            self.officer_id,
            self.reason,
        )


type Message = RiskSegmentChangedMessage | ManualRiskCheckRequiredMessage | RiskCheckResolvedMessage

MESSAGES: dict[str, type[Message]] = {
    "RiskSegmentChanged": RiskSegmentChangedMessage,
    "ManualRiskCheckRequired": ManualRiskCheckRequiredMessage,
    "RiskCheckResolved": RiskCheckResolvedMessage,
}


def parse(data: bytes) -> SegmenterEvent | None:
    """None for an event type Notifier does not handle (#29: consumers filter on type)."""
    message = MESSAGES.get(Envelope.model_validate_json(data).event_type)
    if message is None:
        return None
    return message.model_validate_json(data).to_event()
