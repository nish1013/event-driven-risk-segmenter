from uuid import uuid4

from fastapi.testclient import TestClient
from notifier_db import changed, deliver, opened
from sqlalchemy import Engine

from notifier.adapters.alert_queries import SqlAlertQueries
from notifier.adapters.database import PostgresProbe
from notifier.entrypoints.api import create_app


def api(engine: Engine) -> TestClient:
    return TestClient(create_app(PostgresProbe(engine), SqlAlertQueries(engine)))


def test_us9_alerts_newest_first(engine: Engine, merchant: str) -> None:
    check_id = uuid4()
    deliver(engine, opened(merchant, check_id, "2026-10-20T10:00"))
    deliver(engine, changed(merchant, "medium", "2026-10-01T10:00"))

    body = api(engine).get(f"/merchants/{merchant}/alerts").json()

    assert [(a["kind"], a["status"]) for a in body] == [
        ("risk_check_required", "open"),
        ("segment_changed", None),
    ]


def test_us9_open_alerts_only_the_check(engine: Engine, merchant: str) -> None:
    check_id = uuid4()
    deliver(engine, changed(merchant, "medium", "2026-10-01T10:00"))
    deliver(engine, opened(merchant, check_id, "2026-10-20T10:00"))

    body = api(engine).get(f"/merchants/{merchant}/alerts?status=open").json()

    assert [a["checkId"] for a in body] == [str(check_id)]


def test_us9_unknown_merchant_empty_list(engine: Engine, merchant: str) -> None:
    response = api(engine).get(f"/merchants/{merchant}/alerts")

    assert (response.status_code, response.json()) == (200, [])
