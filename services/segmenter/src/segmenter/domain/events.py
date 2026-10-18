from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from segmenter.domain.check import Decision
from segmenter.domain.policy import Band


@dataclass(frozen=True)
class RiskSegmentChanged:
    event_id: UUID
    merchant_id: str
    occurred_at: datetime
    from_band: Band
    to_band: Band
    rule: str | None
    policy_version: int

    def body(self) -> dict[str, object]:
        return {
            "eventId": str(self.event_id),
            "eventType": "RiskSegmentChanged",
            "merchantId": self.merchant_id,
            "occurredAt": self.occurred_at.isoformat(),
            "from": self.from_band.value,
            "to": self.to_band.value,
            "rule": self.rule,
            "policyVersion": self.policy_version,
        }


@dataclass(frozen=True)
class ManualRiskCheckRequired:
    event_id: UUID
    merchant_id: str
    occurred_at: datetime
    check_id: UUID
    current_band: Band
    proposed_band: Band
    policy_version: int

    def body(self) -> dict[str, object]:
        return {
            "eventId": str(self.event_id),
            "eventType": "ManualRiskCheckRequired",
            "merchantId": self.merchant_id,
            "occurredAt": self.occurred_at.isoformat(),
            "checkId": str(self.check_id),
            "currentBand": self.current_band.value,
            "proposedBand": self.proposed_band.value,
            "policyVersion": self.policy_version,
        }


@dataclass(frozen=True)
class RiskCheckResolved:
    event_id: UUID
    merchant_id: str
    occurred_at: datetime
    check_id: UUID
    decision: Decision
    officer_id: str | None
    reason: str | None

    def body(self) -> dict[str, object]:
        return {
            "eventId": str(self.event_id),
            "eventType": "RiskCheckResolved",
            "merchantId": self.merchant_id,
            "occurredAt": self.occurred_at.isoformat(),
            "checkId": str(self.check_id),
            "decision": self.decision.value,
            "officerId": self.officer_id,
            "reason": self.reason,
        }


type SegmenterEvent = RiskSegmentChanged | ManualRiskCheckRequired | RiskCheckResolved
