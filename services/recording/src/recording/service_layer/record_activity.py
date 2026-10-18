from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from recording.domain.activity import Activity
from recording.domain.errors import ActivityConflict
from recording.domain.schemas import AnyNewActivity
from recording.service_layer.unit_of_work import UnitOfWork


@dataclass(frozen=True)
class Recorded:
    activity: Activity
    created: bool


def record_activity(
    new: AnyNewActivity,
    unit_of_work: UnitOfWork,
    now: Callable[[], datetime],
    new_event_id: Callable[[], UUID],
) -> Recorded:
    candidate = Activity.record(new, recorded_at=now(), event_id=new_event_id())
    with unit_of_work:
        existing = unit_of_work.activities.get(candidate.activity_id)
        if existing is None:
            if unit_of_work.activities.add(candidate):
                unit_of_work.commit()
                return Recorded(candidate, created=True)
            existing = _winner_of_race(unit_of_work, candidate.activity_id)
        if not existing.same_body(candidate):
            raise ActivityConflict(f"activity {candidate.activity_id} exists with another body")
        return Recorded(existing, created=False)


def _winner_of_race(unit_of_work: UnitOfWork, activity_id: str) -> Activity:
    winner = unit_of_work.activities.get(activity_id)
    if winner is None:
        raise RuntimeError(f"activity {activity_id} conflicted on insert but cannot be read")
    return winner
