from abc import ABC, abstractmethod

from sqlalchemy import Connection, select
from sqlalchemy.dialects.postgresql import insert

from recording.adapters.tables import activities
from recording.domain.activity import Activity


class ActivityRepository(ABC):
    def __init__(self) -> None:
        self.seen: list[Activity] = []

    def add(self, activity: Activity) -> bool:
        inserted = self._insert(activity)
        if inserted:
            self.seen.append(activity)
        return inserted

    @abstractmethod
    def get(self, activity_id: str) -> Activity | None: ...

    @abstractmethod
    def _insert(self, activity: Activity) -> bool: ...


class SqlActivityRepository(ActivityRepository):
    def __init__(self, connection: Connection) -> None:
        super().__init__()
        self._connection = connection

    def get(self, activity_id: str) -> Activity | None:
        row = (
            self._connection.execute(
                select(activities).where(activities.c.activity_id == activity_id)
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return None
        return Activity(
            activity_id=row["activity_id"],
            merchant_id=row["merchant_id"],
            type=row["type"],
            occurred_at=row["occurred_at"],
            payload=row["payload"],
            recorded_at=row["recorded_at"],
        )

    def _insert(self, activity: Activity) -> bool:
        # Waits for a concurrent insert of the same id to settle; the loser gets no row back (I1).
        statement = (
            insert(activities)
            .values(
                activity_id=activity.activity_id,
                merchant_id=activity.merchant_id,
                type=activity.type,
                occurred_at=activity.occurred_at,
                payload=activity.payload,
                recorded_at=activity.recorded_at,
            )
            .on_conflict_do_nothing(index_elements=["activity_id"])
            .returning(activities.c.activity_id)
        )
        return self._connection.execute(statement).first() is not None
