import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.exc import ProgrammingError

from segmenter.adapters.database import PostgresProbe
from segmenter.adapters.segment_queries import SqlSegmentQueries
from segmenter.entrypoints.api import create_app, sql_resolver


def test_healthz_ok_against_postgres(engine: Engine) -> None:
    response = TestClient(
        create_app(PostgresProbe(engine), SqlSegmentQueries(engine), sql_resolver(engine))
    ).get("/healthz")

    assert response.status_code == 200


@pytest.mark.parametrize("table", ["recording.activities", "notifier.alerts"])
def test_36_role_refused_on_other_schema(engine: Engine, table: str) -> None:
    with engine.connect() as connection, pytest.raises(ProgrammingError, match="permission denied"):
        connection.execute(text(f"SELECT 1 FROM {table}"))


def test_36_role_cannot_create_tables(engine: Engine) -> None:
    with engine.connect() as connection, pytest.raises(ProgrammingError, match="permission denied"):
        connection.execute(text("CREATE TABLE segmenter.probe (id int)"))
