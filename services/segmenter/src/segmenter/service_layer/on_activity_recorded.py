from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from segmenter.domain.errors import VersionConflict
from segmenter.domain.policy import Policy
from segmenter.domain.segment import Fact
from segmenter.service_layer.unit_of_work import UnitOfWork

MAX_RUNS = 3
FIRST_BACKOFF = 0.05


@dataclass(frozen=True)
class ActivityRecorded:
    event_id: UUID
    fact: Fact


@dataclass(frozen=True)
class Applied:
    runs: int
    duplicate: bool


def on_activity_recorded(
    event: ActivityRecorded,
    unit_of_work: Callable[[], UnitOfWork],
    policy: Policy,
    now: Callable[[], datetime],
    new_event_id: Callable[[], UUID],
    sleep: Callable[[float], None],
) -> Applied:
    # A4: a version conflict reruns the whole use case, each wait longer than the last.
    run = 1
    while True:
        try:
            duplicate = _apply(event, unit_of_work(), policy, now(), new_event_id)
        except VersionConflict:
            if run == MAX_RUNS:
                raise
            sleep(FIRST_BACKOFF * 2 ** (run - 1))
            run += 1
            continue
        return Applied(runs=run, duplicate=duplicate)


def _apply(
    event: ActivityRecorded,
    unit_of_work: UnitOfWork,
    policy: Policy,
    at: datetime,
    new_event_id: Callable[[], UUID],
) -> bool:
    with unit_of_work:
        if not unit_of_work.mark_processed(event.event_id, at):
            unit_of_work.commit()
            return True
        unit_of_work.segments.add_fact(event.fact, policy, at)
        segment = unit_of_work.segments.get(event.fact.merchant_id, policy)
        segment.apply(policy, at, new_event_id())
        unit_of_work.segments.save(segment, policy)
        unit_of_work.commit()
    return False
