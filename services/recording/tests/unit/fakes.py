from typing import Self

from recording.adapters.activity_repository import ActivityRepository
from recording.domain.activity import Activity
from recording.domain.events import ActivityRecorded
from recording.service_layer.unit_of_work import UnitOfWork


class FakeActivityRepository(ActivityRepository):
    def __init__(self) -> None:
        super().__init__()
        self.rows: dict[str, Activity] = {}
        self.hidden_from_get: set[str] = set()

    def get(self, activity_id: str) -> Activity | None:
        if activity_id in self.hidden_from_get:
            self.hidden_from_get.discard(activity_id)
            return None
        return self.rows.get(activity_id)

    def _insert(self, activity: Activity) -> bool:
        if activity.activity_id in self.rows:
            return False
        self.rows[activity.activity_id] = activity
        return True


class FakeUnitOfWork(UnitOfWork):
    def __init__(self) -> None:
        self.activities = FakeActivityRepository()
        self.outbox: list[ActivityRecorded] = []
        self.committed = False
        self._pending: list[ActivityRecorded] = []

    def __enter__(self) -> Self:
        self.committed = False
        return super().__enter__()

    def rollback(self) -> None:
        if not self.committed:
            for activity in self.activities.seen:
                self.activities.rows.pop(activity.activity_id, None)
        self.activities.seen.clear()
        self._pending.clear()

    def _add_to_outbox(self, event: ActivityRecorded) -> None:
        self._pending.append(event)

    def _commit(self) -> None:
        self.outbox.extend(self._pending)
        self._pending.clear()
        self.committed = True
