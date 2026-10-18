from datetime import datetime
from typing import Protocol
from uuid import UUID

from fastapi import FastAPI, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel

from segmenter.adapters.database import PostgresProbe, engine_from_env
from segmenter.adapters.segment_queries import SegmentView, SqlSegmentQueries


class DatabaseProbe(Protocol):
    def is_reachable(self) -> bool: ...


class SegmentQueries(Protocol):
    def segment(self, merchant_id: str) -> SegmentView | None: ...


class Health(BaseModel):
    status: str


class CamelModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel, validate_by_name=True, serialize_by_alias=True
    )


class OpenCheckOut(CamelModel):
    check_id: UUID
    proposed_band: str
    opened_at: datetime


class SegmentOut(CamelModel):
    merchant_id: str
    band: str
    rule: str | None
    policy_version: int
    updated_at: datetime
    open_check: OpenCheckOut | None

    @classmethod
    def of(cls, view: SegmentView) -> SegmentOut:
        open_check = None
        if view.open_check is not None:
            open_check = OpenCheckOut(
                check_id=view.open_check.check_id,
                proposed_band=view.open_check.proposed_band,
                opened_at=view.open_check.opened_at,
            )
        return cls(
            merchant_id=view.merchant_id,
            band=view.band,
            rule=view.rule,
            policy_version=view.policy_version,
            updated_at=view.updated_at,
            open_check=open_check,
        )


def create_app(database: DatabaseProbe, segments: SegmentQueries) -> FastAPI:
    app = FastAPI(title="segmenter")

    @app.get("/healthz")
    def healthz(response: Response) -> Health:
        if database.is_reachable():
            return Health(status="ok")
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return Health(status="database unreachable")

    @app.get("/merchants/{merchant_id}/segment")
    def get_segment(merchant_id: str) -> SegmentOut:
        view = segments.segment(merchant_id)
        if view is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"merchant {merchant_id} not seen")
        return SegmentOut.of(view)

    return app


def create_app_from_env() -> FastAPI:
    engine = engine_from_env()
    return create_app(PostgresProbe(engine), SqlSegmentQueries(engine))
