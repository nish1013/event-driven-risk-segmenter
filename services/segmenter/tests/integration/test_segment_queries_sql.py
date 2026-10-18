from datetime import UTC, datetime
from uuid import uuid4

from fastapi.testclient import TestClient
from segmenter_db import at, deliver, recorded
from sqlalchemy import Engine

from segmenter.adapters.database import PostgresProbe
from segmenter.adapters.segment_queries import SqlSegmentQueries
from segmenter.adapters.tables import checks
from segmenter.entrypoints.api import create_app


def test_us5_segment_with_open_check(engine: Engine, merchant: str) -> None:
    deliver(engine, recorded(merchant, "chargeback", "2026-10-01T00:00"))
    check_id = uuid4()
    with engine.begin() as connection:
        connection.execute(
            checks.insert(),
            [
                {
                    "check_id": uuid4(),
                    "merchant_id": merchant,
                    "proposed_band": "low",
                    "status": "resolved",
                    "decision": "keep_current",
                    "opened_at": at("2026-10-02T00:00"),
                    "resolved_at": at("2026-10-03T00:00"),
                },
                {
                    "check_id": check_id,
                    "merchant_id": merchant,
                    "proposed_band": "low",
                    "status": "open",
                    "decision": None,
                    "opened_at": at("2026-10-20T00:00"),
                    "resolved_at": None,
                },
            ],
        )
    client = TestClient(create_app(PostgresProbe(engine), SqlSegmentQueries(engine)))

    body = client.get(f"/merchants/{merchant}/segment").json()

    assert body["band"] == "medium"
    assert body["rule"] == "chargeback_1_in_30d"
    assert body["policyVersion"] == 1
    assert datetime.fromisoformat(body["updatedAt"]) <= datetime.now(UTC)
    assert body["openCheck"] == {
        "checkId": str(check_id),
        "proposedBand": "low",
        "openedAt": "2026-10-20T00:00:00Z",
    }


def test_us5_unknown_merchant_404(engine: Engine) -> None:
    client = TestClient(create_app(PostgresProbe(engine), SqlSegmentQueries(engine)))

    assert client.get(f"/merchants/m-{uuid4()}/segment").status_code == 404
