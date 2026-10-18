import copy
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Self
from uuid import UUID

from notifier.domain.alert import Alert, AlertStatus
from notifier.service_layer.unit_of_work import UnitOfWork


@dataclass
class Store:
    alerts: dict[UUID, Alert] = field(default_factory=dict)
    processed: set[UUID] = field(default_factory=set)


class FakeAlertRepository:
    def __init__(self, store: Store) -> None:
        self._store = store

    def record(self, alert: Alert) -> Alert | None:
        existing = next(
            (
                stored
                for stored in self._store.alerts.values()
                if alert.check_id is not None and stored.check_id == alert.check_id
            ),
            None,
        )
        if existing is None:
            self._store.alerts[alert.alert_id] = alert
            return alert
        if existing.status is not AlertStatus.OPEN:
            return None
        merged = replace(
            existing,
            status=alert.status,
            decision=alert.decision,
            details=existing.details | alert.details,
            closed_at=alert.closed_at,
        )
        self._store.alerts[existing.alert_id] = merged
        return merged


class FakeUnitOfWork(UnitOfWork):
    """Works on a copy of the store; commit publishes the copy, so rollback undoes everything."""

    def __init__(self, store: Store) -> None:
        self.store = store
        self._working = Store()

    def __enter__(self) -> Self:
        self._working = copy.deepcopy(self.store)
        self.alerts = FakeAlertRepository(self._working)
        return super().__enter__()

    def mark_processed(self, event_id: UUID, at: datetime) -> bool:
        if event_id in self._working.processed:
            return False
        self._working.processed.add(event_id)
        return True

    def commit(self) -> None:
        self.store.__dict__.update(copy.deepcopy(self._working).__dict__)

    def rollback(self) -> None:
        self._working = Store()


class FakeAlertLog:
    def __init__(self, fail: bool = False) -> None:
        self.lines: list[Alert] = []
        self._fail = fail

    def write(self, alert: Alert) -> None:
        if self._fail:
            raise OSError("log sink down")
        self.lines.append(alert)
