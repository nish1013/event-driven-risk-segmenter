from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from segmenter.domain.check import Check, Decision
from segmenter.domain.errors import CheckNotOpen
from segmenter.domain.policy import Band, Policy
from segmenter.service_layer.rerun import rerun_on_conflict
from segmenter.service_layer.unit_of_work import UnitOfWork


@dataclass(frozen=True)
class ResolveRiskCheck:
    check_id: UUID
    decision: Decision
    officer_id: str
    reason: str


class Outcome(StrEnum):
    RESOLVED = "resolved"
    NOT_FOUND = "not_found"
    NOT_OPEN = "not_open"


@dataclass(frozen=True)
class Resolution:
    outcome: Outcome
    check: Check | None = None
    band: Band | None = None
    runs: int = 1


def resolve_risk_check(
    command: ResolveRiskCheck,
    unit_of_work: Callable[[], UnitOfWork],
    policy: Policy,
    now: Callable[[], datetime],
    new_id: Callable[[], UUID],
    sleep: Callable[[float], None],
) -> Resolution:
    ran = rerun_on_conflict(lambda: _resolve(command, unit_of_work(), policy, now(), new_id), sleep)
    return Resolution(ran.result.outcome, ran.result.check, ran.result.band, ran.runs)


def _resolve(
    command: ResolveRiskCheck,
    unit_of_work: UnitOfWork,
    policy: Policy,
    at: datetime,
    new_id: Callable[[], UUID],
) -> Resolution:
    with unit_of_work:
        segment = unit_of_work.segments.get_by_check(command.check_id, policy)
        if segment is None:
            return Resolution(Outcome.NOT_FOUND)
        try:
            check = segment.resolve(
                command.check_id,
                command.decision,
                command.officer_id,
                command.reason,
                policy,
                at,
                new_id,
            )
        except CheckNotOpen:
            return Resolution(Outcome.NOT_OPEN)
        unit_of_work.segments.save(segment, policy)
        unit_of_work.commit()
    return Resolution(Outcome.RESOLVED, check, segment.band)
