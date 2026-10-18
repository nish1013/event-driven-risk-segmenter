from abc import ABC, abstractmethod
from datetime import datetime
from types import TracebackType
from typing import Self
from uuid import UUID

from sqlalchemy import Connection, Engine

from segmenter.adapters.outbox import SqlOutbox
from segmenter.adapters.processed_events import SqlProcessedEvents
from segmenter.adapters.segment_repository import SegmentRepository, SqlSegmentRepository
from segmenter.domain.events import SegmenterEvent


class UnitOfWork(ABC):
    segments: SegmentRepository

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.rollback()

    def commit(self) -> None:
        # A3: each root's events reach the outbox in the order the root raised them.
        for segment in self.segments.seen:
            for event in segment.events:
                self._add_to_outbox(event)
            segment.events.clear()
        self._commit()

    @abstractmethod
    def mark_processed(self, event_id: UUID, at: datetime) -> bool: ...

    @abstractmethod
    def rollback(self) -> None: ...

    @abstractmethod
    def _add_to_outbox(self, event: SegmenterEvent) -> None: ...

    @abstractmethod
    def _commit(self) -> None: ...


class SqlUnitOfWork(UnitOfWork):
    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._connection: Connection | None = None

    def __enter__(self) -> Self:
        self._connection = self._engine.connect()
        self._connection.begin()
        self.segments = SqlSegmentRepository(self._connection)
        self._outbox = SqlOutbox(self._connection)
        self._processed_events = SqlProcessedEvents(self._connection)
        return super().__enter__()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        super().__exit__(exc_type, exc, traceback)
        self._open_connection().close()
        self._connection = None

    def mark_processed(self, event_id: UUID, at: datetime) -> bool:
        return self._processed_events.add(event_id, at)

    def rollback(self) -> None:
        self._open_connection().rollback()

    def _add_to_outbox(self, event: SegmenterEvent) -> None:
        self._outbox.add(event)

    def _commit(self) -> None:
        self._open_connection().commit()

    def _open_connection(self) -> Connection:
        if self._connection is None:
            raise RuntimeError("unit of work used outside its with block")
        return self._connection
