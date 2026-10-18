from fastapi.testclient import TestClient

from notifier.domain.alert import Alert, AlertStatus
from notifier.entrypoints.api import create_app


class FakeProbe:
    def __init__(self, reachable: bool) -> None:
        self._reachable = reachable

    def is_reachable(self) -> bool:
        return self._reachable


class NoAlerts:
    def alerts(self, merchant_id: str, status: AlertStatus | None) -> list[Alert]:
        return []


def test_healthz_ok_when_database_reachable() -> None:
    response = TestClient(create_app(FakeProbe(reachable=True), NoAlerts())).get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_healthz_503_when_database_unreachable() -> None:
    response = TestClient(create_app(FakeProbe(reachable=False), NoAlerts())).get("/healthz")

    assert response.status_code == 503
