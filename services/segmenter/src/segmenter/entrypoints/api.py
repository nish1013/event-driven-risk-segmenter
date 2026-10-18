import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Annotated, Literal, Protocol
from uuid import UUID, uuid4

from fastapi import FastAPI, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict, StringConstraints
from pydantic.alias_generators import to_camel
from sqlalchemy import Engine

from segmenter.adapters.database import PostgresProbe, engine_from_env
from segmenter.adapters.segment_queries import SegmentView, SqlSegmentQueries
from segmenter.domain.check import Check, Decision
from segmenter.domain.errors import VersionConflict
from segmenter.domain.policy import POLICY_V1, Band
from segmenter.service_layer.resolve_risk_check import (
    Outcome,
    Resolution,
    ResolveRiskCheck,
    resolve_risk_check,
)
from segmenter.service_layer.unit_of_work import SqlUnitOfWork

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
type Resolver = Callable[[ResolveRiskCheck], Resolution]


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


class ResolutionIn(CamelModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["approve_lower", "keep_current"]
    officer_id: Text
    reason: Text


class ResolutionOut(CamelModel):
    check_id: UUID
    merchant_id: str
    status: str
    decision: str
    officer_id: str
    reason: str
    resolved_at: datetime
    band: str

    @classmethod
    def of(cls, check: Check, band: Band) -> ResolutionOut:
        if check.decision is None or check.resolved_at is None:
            raise ValueError(f"check {check.check_id} is not resolved")
        return cls(
            check_id=check.check_id,
            merchant_id=check.merchant_id,
            status=check.status.value,
            decision=check.decision.value,
            officer_id=check.officer_id or "",
            reason=check.reason or "",
            resolved_at=check.resolved_at,
            band=band.value,
        )


def create_app(database: DatabaseProbe, segments: SegmentQueries, resolve: Resolver) -> FastAPI:
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

    @app.post("/risk-checks/{check_id}/resolution")
    def resolve_check(check_id: str, body: ResolutionIn) -> ResolutionOut:
        not_found = HTTPException(status.HTTP_404_NOT_FOUND, f"no check {check_id}")
        try:
            command = ResolveRiskCheck(
                UUID(check_id), Decision(body.decision), body.officer_id, body.reason
            )
        except ValueError:
            raise not_found from None
        try:
            resolution = resolve(command)
        except VersionConflict:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE, f"check {check_id} busy, retry"
            ) from None
        if resolution.outcome is Outcome.NOT_FOUND:
            raise not_found
        if resolution.outcome is Outcome.NOT_OPEN:
            raise HTTPException(status.HTTP_409_CONFLICT, f"check {check_id} is not open")
        if resolution.check is None or resolution.band is None:
            raise RuntimeError(f"check {check_id} resolved without a result")
        return ResolutionOut.of(resolution.check, resolution.band)

    return app


def create_app_from_env() -> FastAPI:
    engine = engine_from_env()
    return create_app(PostgresProbe(engine), SqlSegmentQueries(engine), sql_resolver(engine))


def sql_resolver(engine: Engine) -> Resolver:
    def resolve(command: ResolveRiskCheck) -> Resolution:
        return resolve_risk_check(
            command,
            lambda: SqlUnitOfWork(engine),
            POLICY_V1,
            lambda: datetime.now(UTC),
            uuid4,
            time.sleep,
        )

    return resolve
