import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.exc import ProgrammingError

from notifier.adapters.database import PostgresProbe
from notifier.entrypoints.api import create_app


def test_healthz_ok_against_postgres(engine: Engine) -> None:
    response = TestClient(create_app(PostgresProbe(engine))).get("/healthz")

    assert response.status_code == 200


@pytest.mark.parametrize("table", ["recording.activities", "segmenter.segments"])
def test_36_role_refused_on_other_schema(engine: Engine, table: str) -> None:
    with engine.connect() as connection, pytest.raises(ProgrammingError, match="permission denied"):
        connection.execute(text(f"SELECT 1 FROM {table}"))


def test_36_role_cannot_create_tables(engine: Engine) -> None:
    with engine.connect() as connection, pytest.raises(ProgrammingError, match="permission denied"):
        connection.execute(text("CREATE TABLE notifier.probe (id int)"))
