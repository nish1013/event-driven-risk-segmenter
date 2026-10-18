import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Connection, Engine, TextClause, create_engine, func, select, text

from recording.adapters.database import PostgresProbe
from recording.adapters.tables import activities, outbox
from recording.entrypoints.api import create_app
from recording.service_layer.unit_of_work import SqlUnitOfWork

CONCURRENT = 20


def chargeback(activity_id: str) -> dict[str, Any]:
    return {
        "activityId": activity_id,
        "merchantId": f"m-{activity_id}",
        "type": "chargeback",
        "occurredAt": "2026-10-01T10:00:00Z",
        "payload": {
            "transactionId": "t-9",
            "amount": "120.00",
            "currency": "GBP",
            "reasonCode": "4837",
        },
    }


@pytest.fixture
def wide_engine(database_url: str) -> Any:
    engine = create_engine(database_url, pool_size=CONCURRENT + 2)
    yield engine
    engine.dispose()


def app_on(engine: Engine) -> FastAPI:
    return create_app(PostgresProbe(engine), lambda: SqlUnitOfWork(engine))


def stored(engine: Engine, activity_id: str) -> tuple[int, int]:
    with engine.connect() as connection:
        rows = connection.scalar(
            select(func.count()).where(activities.c.activity_id == activity_id)
        )
        events = connection.scalar(select(func.count()).where(outbox.c.activity_id == activity_id))
    return rows or 0, events or 0


def all_waited_on_lock(engine: Engine, expected: int) -> bool:
    query = text(
        "SELECT count(*) FROM pg_stat_activity "
        "WHERE usename = current_user AND wait_event_type = 'Lock' "
        "AND query LIKE 'INSERT INTO recording.activities%'"
    )
    deadline = time.monotonic() + 10
    with engine.connect() as connection:
        while _scalar(connection, query) < expected:
            if time.monotonic() > deadline:
                return False
            time.sleep(0.05)
    return True


def test_i1_i3_concurrent_posts_one_row_one_event(wide_engine: Engine) -> None:
    activity_id = f"a-{uuid4()}"
    app = app_on(wide_engine)

    def post() -> int:
        return TestClient(app).post("/activities", json=chargeback(activity_id)).status_code

    # Hold the id in an open transaction so every request reaches the insert before any commits.
    with ThreadPoolExecutor(CONCURRENT) as pool, wide_engine.connect() as holder:
        holder.begin()
        holder.execute(
            activities.insert().values(
                activity_id=activity_id,
                merchant_id="m-hold",
                type="chargeback",
                occurred_at=datetime.now(UTC),
                payload={},
                recorded_at=datetime.now(UTC),
            )
        )
        responses = [pool.submit(post) for _ in range(CONCURRENT)]
        raced = all_waited_on_lock(wide_engine, CONCURRENT)
        holder.rollback()
        statuses = sorted(response.result() for response in responses)

    assert statuses == [200] * (CONCURRENT - 1) + [201]
    assert stored(wide_engine, activity_id) == (1, 1)
    assert raced, "requests did not all reach the insert while the id was held"


def test_us1_other_body_409_against_stored_row(engine: Engine) -> None:
    activity_id = f"a-{uuid4()}"
    client = TestClient(app_on(engine))
    client.post("/activities", json=chargeback(activity_id))

    response = client.post(
        "/activities",
        json=chargeback(activity_id)
        | {"payload": chargeback(activity_id)["payload"] | {"amount": "1.00"}},
    )

    assert response.status_code == 409
    assert stored(engine, activity_id) == (1, 1)


def _scalar(connection: Connection, query: TextClause) -> int:
    # pg_stat_activity is a snapshot per transaction; end it so each poll reads fresh state.
    value = connection.scalar(query) or 0
    connection.rollback()
    return value
