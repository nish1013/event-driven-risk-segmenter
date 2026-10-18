from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

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
