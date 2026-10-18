from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from segmenter.domain.events import RiskSegmentChanged
from segmenter.domain.policy import Band, Policy


@dataclass(frozen=True)
class Fact:
    activity_id: str
    merchant_id: str
    type: str
    occurred_at: datetime


@dataclass(frozen=True)
class FactCounts:
    # P1: facts per rule window, ending at the anchor. Counted in SQL, never loaded (#39).
    anchor: datetime | None
    per_rule: dict[str, int]


@dataclass
class Segment:
    merchant_id: str
    band: Band
    rule: str | None
    policy_version: int
    version_number: int
    updated_at: datetime
    fact_counts: FactCounts
    events: list[RiskSegmentChanged] = field(default_factory=list, compare=False, repr=False)

    def apply(self, policy: Policy, at: datetime, event_id: UUID) -> None:
        result = policy.rule_band(self.fact_counts.per_rule)
        # P4: only a rise applies here. A lower rule band never lowers the band (#18).
        if result.band.rank <= self.band.rank:
            return
        self.events.append(
            RiskSegmentChanged(
                event_id=event_id,
                merchant_id=self.merchant_id,
                occurred_at=at,
                from_band=self.band,
                to_band=result.band,
                rule=result.rule,
                policy_version=policy.version,
            )
        )
        self.band = result.band
        self.rule = result.rule
        self.policy_version = policy.version
        self.updated_at = at
