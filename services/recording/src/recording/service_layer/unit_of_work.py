from abc import ABC, abstractmethod
from types import TracebackType
from typing import Self

from sqlalchemy import Connection, Engine

from recording.adapters.activity_repository import ActivityRepository, SqlActivityRepository
from recording.adapters.outbox import SqlOutbox
from recording.domain.events import ActivityRecorded


class UnitOfWork(ABC):
    activities: ActivityRepository

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
        for activity in self.activities.seen:
            for event in activity.events:
                self._add_to_outbox(event)
            activity.events.clear()
        self._commit()

    @abstractmethod
    def rollback(self) -> None: ...

    @abstractmethod
    def _add_to_outbox(self, event: ActivityRecorded) -> None: ...

    @abstractmethod
    def _commit(self) -> None: ...


class SqlUnitOfWork(UnitOfWork):
    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._connection: Connection | None = None

    def __enter__(self) -> Self:
        self._connection = self._engine.connect()
        self._connection.begin()
        self.activities = SqlActivityRepository(self._connection)
        self._outbox = SqlOutbox(self._connection)
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

    def rollback(self) -> None:
        self._open_connection().rollback()

    def _add_to_outbox(self, event: ActivityRecorded) -> None:
        self._outbox.add(event)

    def _commit(self) -> None:
        self._open_connection().commit()

    def _open_connection(self) -> Connection:
        if self._connection is None:
            raise RuntimeError("unit of work used outside its with block")
        return self._connection
