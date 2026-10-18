from fastapi.testclient import TestClient

from segmenter.adapters.segment_queries import SegmentView
from segmenter.entrypoints.api import create_app
from segmenter.service_layer.resolve_risk_check import Outcome, Resolution, ResolveRiskCheck


class FakeProbe:
    def __init__(self, reachable: bool) -> None:
        self._reachable = reachable

    def is_reachable(self) -> bool:
        return self._reachable


class NoSegments:
    def segment(self, merchant_id: str) -> SegmentView | None:
        return None


def no_checks(_: ResolveRiskCheck) -> Resolution:
    return Resolution(Outcome.NOT_FOUND)


def test_healthz_ok_when_database_reachable() -> None:
    response = TestClient(create_app(FakeProbe(reachable=True), NoSegments(), no_checks)).get(
        "/healthz"
    )

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_healthz_503_when_database_unreachable() -> None:
    response = TestClient(create_app(FakeProbe(reachable=False), NoSegments(), no_checks)).get(
        "/healthz"
    )

    assert response.status_code == 503
