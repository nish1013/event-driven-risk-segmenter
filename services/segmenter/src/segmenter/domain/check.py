from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from segmenter.domain.policy import Band


class CheckStatus(StrEnum):
    OPEN = "open"
    RESOLVED = "resolved"


class Decision(StrEnum):
    APPROVE_LOWER = "approve_lower"
    KEEP_CURRENT = "keep_current"
    SUPERSEDED = "superseded"


@dataclass(frozen=True)
class Check:
    check_id: UUID
    merchant_id: str
    proposed_band: Band
    status: CheckStatus
    decision: Decision | None
    officer_id: str | None
    reason: str | None
    opened_at: datetime
    resolved_at: datetime | None

    @classmethod
    def open(cls, check_id: UUID, merchant_id: str, proposed_band: Band, at: datetime) -> Check:
        return cls(
            check_id=check_id,
            merchant_id=merchant_id,
            proposed_band=proposed_band,
            status=CheckStatus.OPEN,
            decision=None,
            officer_id=None,
            reason=None,
            opened_at=at,
            resolved_at=None,
        )

    @property
    def is_open(self) -> bool:
        return self.status is CheckStatus.OPEN

    def propose(self, band: Band) -> Check:
        return replace(self, proposed_band=band)

    def resolve(
        self, decision: Decision, officer_id: str | None, reason: str | None, at: datetime
    ) -> Check:
        return replace(
            self,
            status=CheckStatus.RESOLVED,
            decision=decision,
            officer_id=officer_id,
            reason=reason,
            resolved_at=at,
        )
