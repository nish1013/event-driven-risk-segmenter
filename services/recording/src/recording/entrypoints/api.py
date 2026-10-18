from typing import Protocol

from fastapi import FastAPI, Response, status
from pydantic import BaseModel

from recording.adapters.database import PostgresProbe, engine_from_env


class DatabaseProbe(Protocol):
    def is_reachable(self) -> bool: ...


class Health(BaseModel):
    status: str


def create_app(database: DatabaseProbe) -> FastAPI:
    app = FastAPI(title="recording")

    @app.get("/healthz")
    def healthz(response: Response) -> Health:
        if database.is_reachable():
            return Health(status="ok")
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return Health(status="database unreachable")

    return app


def create_app_from_env() -> FastAPI:
    return create_app(PostgresProbe(engine_from_env()))
