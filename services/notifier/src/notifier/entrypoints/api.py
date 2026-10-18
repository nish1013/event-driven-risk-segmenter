from datetime import datetime
from typing import Annotated, Any, Protocol
from uuid import UUID

from fastapi import FastAPI, Query, Response, status
from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel

from notifier.adapters.alert_queries import SqlAlertQueries
from notifier.adapters.database import PostgresProbe, engine_from_env
from notifier.domain.alert import Alert, AlertStatus


class DatabaseProbe(Protocol):
    def is_reachable(self) -> bool: ...


class AlertQueries(Protocol):
    def alerts(self, merchant_id: str, status: AlertStatus | None) -> list[Alert]: ...


class Health(BaseModel):
    status: str


class AlertOut(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel, validate_by_name=True, serialize_by_alias=True
    )

    alert_id: UUID
    merchant_id: str
    kind: str
    check_id: UUID | None
    status: str | None
    decision: str | None
    details: dict[str, Any]
    created_at: datetime
    closed_at: datetime | None

    @classmethod
    def of(cls, alert: Alert) -> AlertOut:
        return cls(
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


def create_app(database: DatabaseProbe, alerts: AlertQueries) -> FastAPI:
    app = FastAPI(title="notifier")

    @app.get("/healthz")
    def healthz(response: Response) -> Health:
        if database.is_reachable():
            return Health(status="ok")
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return Health(status="database unreachable")

    @app.get("/merchants/{merchant_id}/alerts")
    def list_alerts(
        merchant_id: str,
        alert_status: Annotated[AlertStatus | None, Query(alias="status")] = None,
    ) -> list[AlertOut]:
        return [AlertOut.of(alert) for alert in alerts.alerts(merchant_id, alert_status)]

    return app


def create_app_from_env() -> FastAPI:
    engine = engine_from_env()
    return create_app(PostgresProbe(engine), SqlAlertQueries(engine))
