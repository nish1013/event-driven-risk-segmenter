from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from notifier.domain.events import (
    Decision,
    ManualRiskCheckRequired,
    RiskCheckResolved,
    RiskSegmentChanged,
    SegmenterEvent,
)


class AlertKind(StrEnum):
    SEGMENT_CHANGED = "segment_changed"
    RISK_CHECK_REQUIRED = "risk_check_required"


class AlertStatus(StrEnum):
    OPEN = "open"
    CLOSED = "closed"


@dataclass(frozen=True)
class Alert:
    alert_id: UUID
    merchant_id: str
    kind: AlertKind
    check_id: UUID | None
    status: AlertStatus | None
    decision: Decision | None
    details: dict[str, Any]
    created_at: datetime
    closed_at: datetime | None

    @classmethod
    def of(cls, event: SegmenterEvent, alert_id: UUID) -> Alert:
        match event:
            case RiskSegmentChanged():
                return cls._segment_changed(event, alert_id)
            case ManualRiskCheckRequired():
                return cls._check_opened(event, alert_id)
            case RiskCheckResolved():
                return cls._check_closed(event, alert_id)

    @classmethod
    def _segment_changed(cls, event: RiskSegmentChanged, alert_id: UUID) -> Alert:
        return cls(
            alert_id=alert_id,
            merchant_id=event.merchant_id,
            kind=AlertKind.SEGMENT_CHANGED,
            check_id=None,
            status=None,
            decision=None,
            details={
                "from": event.from_band,
                "to": event.to_band,
                "rule": event.rule,
                "policyVersion": event.policy_version,
            },
            created_at=event.occurred_at,
            closed_at=None,
        )

    @classmethod
    def _check_opened(cls, event: ManualRiskCheckRequired, alert_id: UUID) -> Alert:
        return cls(
            alert_id=alert_id,
            merchant_id=event.merchant_id,
            kind=AlertKind.RISK_CHECK_REQUIRED,
            check_id=event.check_id,
            status=AlertStatus.OPEN,
            decision=None,
            details={
                "currentBand": event.current_band,
                "proposedBand": event.proposed_band,
                "policyVersion": event.policy_version,
            },
            created_at=event.occurred_at,
            closed_at=None,
        )

    @classmethod
    def _check_closed(cls, event: RiskCheckResolved, alert_id: UUID) -> Alert:
        # Delivery order is not guaranteed (#43): "resolved" may arrive before "opened",
        # so it carries everything a closed alert needs on its own.
        return cls(
            alert_id=alert_id,
            merchant_id=event.merchant_id,
            kind=AlertKind.RISK_CHECK_REQUIRED,
            check_id=event.check_id,
            status=AlertStatus.CLOSED,
            decision=event.decision,
            details={"officerId": event.officer_id, "reason": event.reason},
            created_at=event.occurred_at,
            closed_at=event.occurred_at,
        )
