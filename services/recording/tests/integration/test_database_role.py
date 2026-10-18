import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.exc import ProgrammingError

from recording.adapters.database import PostgresProbe
from recording.entrypoints.api import create_app
from recording.service_layer.unit_of_work import SqlUnitOfWork


def test_healthz_ok_against_postgres(engine: Engine) -> None:
    response = TestClient(create_app(PostgresProbe(engine), lambda: SqlUnitOfWork(engine))).get(
        "/healthz"
    )

    assert response.status_code == 200


@pytest.mark.parametrize("table", ["segmenter.segments", "notifier.alerts"])
def test_36_role_refused_on_other_schema(engine: Engine, table: str) -> None:
    with engine.connect() as connection, pytest.raises(ProgrammingError, match="permission denied"):
        connection.execute(text(f"SELECT 1 FROM {table}"))


def test_36_role_cannot_create_tables(engine: Engine) -> None:
    with engine.connect() as connection, pytest.raises(ProgrammingError, match="permission denied"):
        connection.execute(text("CREATE TABLE recording.probe (id int)"))


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE recording.activities SET merchant_id = 'm-x'",
        "DELETE FROM recording.activities",
        "TRUNCATE recording.activities",
        "UPDATE recording.outbox SET body = '{}'",
    ],
)
def test_i2_role_cannot_change_activities(engine: Engine, statement: str) -> None:
    with engine.connect() as connection, pytest.raises(ProgrammingError, match="permission denied"):
        connection.execute(text(statement))
