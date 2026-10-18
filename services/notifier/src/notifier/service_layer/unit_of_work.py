from abc import ABC, abstractmethod
from datetime import datetime
from types import TracebackType
from typing import Self
from uuid import UUID

from sqlalchemy import Connection, Engine

from notifier.adapters.alert_repository import AlertRepository, SqlAlertRepository
from notifier.adapters.processed_events import SqlProcessedEvents


class UnitOfWork(ABC):
    alerts: AlertRepository

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.rollback()

    @abstractmethod
    def mark_processed(self, event_id: UUID, at: datetime) -> bool: ...

    @abstractmethod
    def commit(self) -> None: ...

    @abstractmethod
    def rollback(self) -> None: ...


class SqlUnitOfWork(UnitOfWork):
    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._connection: Connection | None = None

    def __enter__(self) -> Self:
        self._connection = self._engine.connect()
        self._connection.begin()
        self.alerts = SqlAlertRepository(self._connection)
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

    def commit(self) -> None:
        self._open_connection().commit()

    def rollback(self) -> None:
        self._open_connection().rollback()

    def _open_connection(self) -> Connection:
        if self._connection is None:
            raise RuntimeError("unit of work used outside its with block")
        return self._connection
