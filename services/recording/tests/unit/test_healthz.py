from fastapi.testclient import TestClient

from recording.entrypoints.api import create_app


class FakeProbe:
    def __init__(self, reachable: bool) -> None:
        self._reachable = reachable

    def is_reachable(self) -> bool:
        return self._reachable


def test_healthz_ok_when_database_reachable() -> None:
    response = TestClient(create_app(FakeProbe(reachable=True))).get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_healthz_503_when_database_unreachable() -> None:
    response = TestClient(create_app(FakeProbe(reachable=False))).get("/healthz")

    assert response.status_code == 503
