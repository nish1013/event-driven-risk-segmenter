from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from segmenter.domain.check import Check, Decision
from segmenter.domain.errors import CheckNotOpen, InvariantViolation
from segmenter.domain.events import (
    ManualRiskCheckRequired,
    RiskCheckResolved,
    RiskSegmentChanged,
    SegmenterEvent,
)
from segmenter.domain.policy import Band, Policy, RuleResult


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
    checks: list[Check] = field(default_factory=list)
    events: list[SegmenterEvent] = field(default_factory=list, compare=False, repr=False)

    @property
    def open_check(self) -> Check | None:
        return next((check for check in self.checks if check.is_open), None)

    def apply(self, policy: Policy, at: datetime, new_id: Callable[[], UUID]) -> None:
        # P4. A rule band equal to the band also ends an open check: approving its lower
        # proposal would set the band below the rule band (I6, plan-deviations D4).
        result = policy.rule_band(self.fact_counts.per_rule)
        if result.band.rank >= self.band.rank:
            self._supersede_open_check(at, new_id)
            if result.band.rank > self.band.rank:
                self._change_band(result, policy, at, new_id)
        else:
            self._hold(result.band, policy, at, new_id)

    def resolve(
        self,
        check_id: UUID,
        decision: Decision,
        officer_id: str,
        reason: str,
        policy: Policy,
        at: datetime,
        new_id: Callable[[], UUID],
    ) -> Check:
        # P5. Only an officer's decision comes here; superseded is set by apply.
        if decision is Decision.SUPERSEDED:
            raise ValueError("superseded is not an officer's decision")
        check = self.open_check
        if check is None or check.check_id != check_id:
            raise CheckNotOpen(check_id)
        resolved = self._close(check, decision, officer_id, reason, at, new_id)
        if decision is Decision.APPROVE_LOWER:
            rule = policy.rule_band(self.fact_counts.per_rule).rule
            self._change_band(RuleResult(check.proposed_band, rule), policy, at, new_id)
        return resolved

    def assert_invariants(self) -> None:
        open_checks = [check for check in self.checks if check.is_open]
        if len(open_checks) > 1:
            raise InvariantViolation(f"{self.merchant_id} has {len(open_checks)} open checks (I7)")
        for check in open_checks:
            if check.proposed_band.rank >= self.band.rank:
                raise InvariantViolation(
                    f"check {check.check_id} proposes {check.proposed_band} on a {self.band} "
                    "segment (I8)"
                )

    def _hold(self, band: Band, policy: Policy, at: datetime, new_id: Callable[[], UUID]) -> None:
        check = self.open_check
        if check is not None:
            self._replace(check.propose(band))
            return
        check = Check.open(new_id(), self.merchant_id, band, at)
        self.checks.append(check)
        self.events.append(
            ManualRiskCheckRequired(
                event_id=new_id(),
                merchant_id=self.merchant_id,
                occurred_at=at,
                check_id=check.check_id,
                current_band=self.band,
                proposed_band=band,
                policy_version=policy.version,
            )
        )

    def _supersede_open_check(self, at: datetime, new_id: Callable[[], UUID]) -> None:
        check = self.open_check
        if check is not None:
            self._close(check, Decision.SUPERSEDED, None, None, at, new_id)

    def _close(
        self,
        check: Check,
        decision: Decision,
        officer_id: str | None,
        reason: str | None,
        at: datetime,
        new_id: Callable[[], UUID],
    ) -> Check:
        resolved = check.resolve(decision, officer_id, reason, at)
        self._replace(resolved)
        self.events.append(
            RiskCheckResolved(
                event_id=new_id(),
                merchant_id=self.merchant_id,
                occurred_at=at,
                check_id=check.check_id,
                decision=decision,
                officer_id=officer_id,
                reason=reason,
            )
        )
        return resolved

    def _change_band(
        self, result: RuleResult, policy: Policy, at: datetime, new_id: Callable[[], UUID]
    ) -> None:
        self.events.append(
            RiskSegmentChanged(
                event_id=new_id(),
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

    def _replace(self, check: Check) -> None:
        self.checks = [check if c.check_id == check.check_id else c for c in self.checks]
