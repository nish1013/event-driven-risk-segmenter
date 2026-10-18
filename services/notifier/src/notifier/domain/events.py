from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID


class Decision(StrEnum):
    APPROVE_LOWER = "approve_lower"
    KEEP_CURRENT = "keep_current"
    SUPERSEDED = "superseded"


@dataclass(frozen=True)
class RiskSegmentChanged:
    event_id: UUID
    merchant_id: str
    occurred_at: datetime
    from_band: str
    to_band: str
    rule: str | None
    policy_version: int


@dataclass(frozen=True)
class ManualRiskCheckRequired:
    event_id: UUID
    merchant_id: str
    occurred_at: datetime
    check_id: UUID
    current_band: str
    proposed_band: str
    policy_version: int


@dataclass(frozen=True)
class RiskCheckResolved:
    event_id: UUID
    merchant_id: str
    occurred_at: datetime
    check_id: UUID
    decision: Decision
    officer_id: str | None
    reason: str | None


type SegmenterEvent = RiskSegmentChanged | ManualRiskCheckRequired | RiskCheckResolved
