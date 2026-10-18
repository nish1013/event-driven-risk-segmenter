from sqlalchemy import Engine, select

from notifier.adapters.alert_repository import alert_from_row
from notifier.adapters.tables import alerts
from notifier.domain.alert import Alert, AlertStatus


class SqlAlertQueries:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def alerts(self, merchant_id: str, status: AlertStatus | None) -> list[Alert]:
        query = (
            select(alerts)
            .where(alerts.c.merchant_id == merchant_id)
            .order_by(alerts.c.created_at.desc(), alerts.c.alert_id)
        )
        if status is not None:
            query = query.where(alerts.c.status == status.value)
        with self._engine.connect() as connection:
            return [alert_from_row(row) for row in connection.execute(query).mappings()]
