from datetime import datetime
from uuid import UUID

from sqlalchemy import Connection, delete
from sqlalchemy.dialects.postgresql import insert

from segmenter.adapters.tables import processed_events


class SqlProcessedEvents:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def add(self, event_id: UUID, processed_at: datetime) -> bool:
        # I10: a redelivery waits here for the first delivery's transaction, then finds the row.
        result = self._connection.execute(
            insert(processed_events)
            .values(event_id=event_id, processed_at=processed_at)
            .on_conflict_do_nothing(index_elements=["event_id"])
            .returning(processed_events.c.event_id)
        )
        return result.first() is not None

    def delete_before(self, cutoff: datetime) -> int:
        result = self._connection.execute(
            delete(processed_events).where(processed_events.c.processed_at < cutoff)
        )
        return result.rowcount
