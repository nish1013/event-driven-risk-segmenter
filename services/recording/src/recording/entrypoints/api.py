from collections.abc import Callable
from datetime import UTC, datetime
from typing import Annotated, Any, Protocol
from uuid import UUID, uuid4

from fastapi import Body, FastAPI, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel

from recording.adapters.database import PostgresProbe, engine_from_env
from recording.domain.activity import Activity
from recording.domain.errors import ActivityConflict, OccurredAtInFuture
from recording.domain.schemas import AnyNewActivity
from recording.service_layer.record_activity import record_activity
from recording.service_layer.unit_of_work import SqlUnitOfWork, UnitOfWork


class DatabaseProbe(Protocol):
    def is_reachable(self) -> bool: ...


class Health(BaseModel):
    status: str


class ActivityOut(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel, validate_by_name=True, serialize_by_alias=True
    )

    activity_id: str
    merchant_id: str
    type: str
    occurred_at: datetime
    payload: dict[str, Any]
    recorded_at: datetime

    @classmethod
    def of(cls, activity: Activity) -> ActivityOut:
        return cls(
            activity_id=activity.activity_id,
            merchant_id=activity.merchant_id,
            type=activity.type,
            occurred_at=activity.occurred_at,
            payload=activity.payload,
            recorded_at=activity.recorded_at,
        )


def create_app(
    database: DatabaseProbe,
    unit_of_work: Callable[[], UnitOfWork],
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    new_event_id: Callable[[], UUID] = uuid4,
) -> FastAPI:
    app = FastAPI(title="recording")

    @app.get("/healthz")
    def healthz(response: Response) -> Health:
        if database.is_reachable():
            return Health(status="ok")
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return Health(status="database unreachable")

    @app.post("/activities", status_code=status.HTTP_201_CREATED)
    def post_activity(
        new: Annotated[AnyNewActivity, Body(discriminator="type")], response: Response
    ) -> ActivityOut:
        try:
            recorded = record_activity(new, unit_of_work(), now, new_event_id)
        except OccurredAtInFuture as error:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error
        except ActivityConflict as error:
            raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
        if not recorded.created:
            response.status_code = status.HTTP_200_OK
        return ActivityOut.of(recorded.activity)

    return app


def create_app_from_env() -> FastAPI:
    engine = engine_from_env()
    return create_app(PostgresProbe(engine), lambda: SqlUnitOfWork(engine))
