from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import Connection, delete, select, update

from segmenter.adapters.tables import outbox
from segmenter.domain.events import RiskSegmentChanged

SEGMENTER_EVENTS_TOPIC = "segmenter-events"


@dataclass(frozen=True)
class OutboxMessage:
    id: int
    event_id: UUID
    topic: str
    ordering_key: str
    body: dict[str, object]


class SqlOutbox:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def add(self, event: RiskSegmentChanged) -> None:
        self._connection.execute(
            outbox.insert().values(
                event_id=event.event_id,
                topic=SEGMENTER_EVENTS_TOPIC,
                ordering_key=event.merchant_id,
                body=event.body(),
                created_at=event.occurred_at,
            )
        )

    def unsent(self, limit: int) -> list[OutboxMessage]:
        rows = self._connection.execute(
            select(
                outbox.c.id, outbox.c.event_id, outbox.c.topic, outbox.c.ordering_key, outbox.c.body
            )
            .where(outbox.c.sent_at.is_(None))
            .order_by(outbox.c.id)
            .limit(limit)
        ).mappings()
        return [OutboxMessage(**row) for row in rows]

    def mark_sent(self, ids: list[int], sent_at: datetime) -> None:
        if ids:
            self._connection.execute(
                update(outbox).where(outbox.c.id.in_(ids)).values(sent_at=sent_at)
            )

    def delete_sent_before(self, cutoff: datetime) -> int:
        result = self._connection.execute(delete(outbox).where(outbox.c.sent_at < cutoff))
        return result.rowcount
