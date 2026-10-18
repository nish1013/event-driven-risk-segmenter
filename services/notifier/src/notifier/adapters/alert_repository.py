from typing import Protocol

from sqlalchemy import Connection
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import RowMapping

from notifier.adapters.tables import alerts
from notifier.domain.alert import Alert, AlertKind, AlertStatus
from notifier.domain.events import Decision


class AlertRepository(Protocol):
    def record(self, alert: Alert) -> Alert | None: ...


class SqlAlertRepository:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def record(self, alert: Alert) -> Alert | None:
        statement = insert(alerts).values(
            alert_id=alert.alert_id,
            merchant_id=alert.merchant_id,
            kind=alert.kind.value,
            check_id=alert.check_id,
            status=None if alert.status is None else alert.status.value,
            decision=None if alert.decision is None else alert.decision.value,
            details=alert.details,
            created_at=alert.created_at,
            closed_at=alert.closed_at,
        )
        if alert.check_id is not None:
            # I13: one alert per check. I14: the stored row changes only while it is open, so a
            # closed alert stays closed whichever check event arrives after it.
            statement = statement.on_conflict_do_update(
                index_elements=[alerts.c.check_id],
                set_={
                    "status": statement.excluded.status,
                    "decision": statement.excluded.decision,
                    "details": alerts.c.details.op("||")(statement.excluded.details),
                    "closed_at": statement.excluded.closed_at,
                },
                where=alerts.c.status == AlertStatus.OPEN.value,
            )
        row = self._connection.execute(statement.returning(*alerts.c)).mappings().first()
        return None if row is None else alert_from_row(row)


def alert_from_row(row: RowMapping) -> Alert:
    return Alert(
        alert_id=row["alert_id"],
        merchant_id=row["merchant_id"],
        kind=AlertKind(row["kind"]),
        check_id=row["check_id"],
        status=None if row["status"] is None else AlertStatus(row["status"]),
        decision=None if row["decision"] is None else Decision(row["decision"]),
        details=row["details"],
        created_at=row["created_at"],
        closed_at=row["closed_at"],
    )
