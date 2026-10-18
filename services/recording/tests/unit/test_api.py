from typing import Any

import pytest
from fakes import FakeUnitOfWork
from fastapi.testclient import TestClient
from samples import NOW, chargeback

from recording.entrypoints.api import create_app


class ReachableProbe:
    def is_reachable(self) -> bool:
        return True


@pytest.fixture
def unit_of_work() -> FakeUnitOfWork:
    return FakeUnitOfWork()


@pytest.fixture
def client(unit_of_work: FakeUnitOfWork) -> TestClient:
    return TestClient(create_app(ReachableProbe(), lambda: unit_of_work, now=lambda: NOW))


def test_us1_new_activity_201(client: TestClient, unit_of_work: FakeUnitOfWork) -> None:
    response = client.post("/activities", json=chargeback())

    assert response.status_code == 201
    assert response.json() == {
        "activityId": "a-1",
        "merchantId": "m-100",
        "type": "chargeback",
        "occurredAt": "2026-10-01T10:00:00Z",
        "payload": chargeback()["payload"],
        "recordedAt": "2026-10-01T10:00:00Z",
    }
    assert len(unit_of_work.outbox) == 1


def test_us1_same_body_200_original(client: TestClient, unit_of_work: FakeUnitOfWork) -> None:
    first = client.post("/activities", json=chargeback())

    repeat = client.post("/activities", json=chargeback())

    assert repeat.status_code == 200
    assert repeat.json() == first.json()
    assert len(unit_of_work.outbox) == 1


def test_us1_other_type_409(client: TestClient, unit_of_work: FakeUnitOfWork) -> None:
    client.post("/activities", json=chargeback())

    response = client.post(
        "/activities",
        json=chargeback(type="failed_login", payload={"userId": "u-1", "method": "password"}),
    )

    assert response.status_code == 409
    assert unit_of_work.activities.rows["a-1"].type == "chargeback"
    assert len(unit_of_work.outbox) == 1


def without(body: dict[str, Any], key: str) -> dict[str, Any]:
    return {name: value for name, value in body.items() if name != key}


@pytest.mark.parametrize(
    "body",
    [
        pytest.param(chargeback(type="refund"), id="refund"),
        pytest.param(without(chargeback(), "activityId"), id="no-activity-id"),
        pytest.param(
            chargeback(payload=without(chargeback()["payload"], "currency")), id="no-currency"
        ),
        pytest.param(
            chargeback(
                type="failed_login",
                payload={"userId": "u-1", "method": "password", "ip": "10.0.0.1"},
            ),
            id="failed-login-ip",
        ),
        pytest.param(chargeback(occurredAt="2026-10-01T10:06:00Z"), id="future-10:06"),
    ],
)
def test_us1_invalid_422_nothing_stored(
    client: TestClient, unit_of_work: FakeUnitOfWork, body: dict[str, Any]
) -> None:
    response = client.post("/activities", json=body)

    assert response.status_code == 422
    assert unit_of_work.activities.rows == {}
    assert unit_of_work.outbox == []


def test_30_unknown_type_rejected_by_type_key(client: TestClient) -> None:
    response = client.post("/activities", json=chargeback(type="refund"))

    assert [error["type"] for error in response.json()["detail"]] == ["union_tag_invalid"]
