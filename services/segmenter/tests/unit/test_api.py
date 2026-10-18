from uuid import UUID

from fastapi.testclient import TestClient
from samples import NOW, at
from test_healthz import FakeProbe

from segmenter.adapters.segment_queries import OpenCheckView, SegmentView
from segmenter.entrypoints.api import create_app

CHECK_ID = UUID(int=1)


class FakeQueries:
    def __init__(self, *views: SegmentView) -> None:
        self._views = {view.merchant_id: view for view in views}

    def segment(self, merchant_id: str) -> SegmentView | None:
        return self._views.get(merchant_id)


def client(*views: SegmentView) -> TestClient:
    return TestClient(create_app(FakeProbe(reachable=True), FakeQueries(*views)))


def test_us5_segment_with_open_check() -> None:
    view = SegmentView(
        "m-100",
        "high",
        "chargeback_3_in_30d",
        1,
        NOW,
        OpenCheckView(CHECK_ID, "low", at("2026-10-20T00:00")),
    )

    response = client(view).get("/merchants/m-100/segment")

    assert response.status_code == 200
    assert response.json() == {
        "merchantId": "m-100",
        "band": "high",
        "rule": "chargeback_3_in_30d",
        "policyVersion": 1,
        "updatedAt": "2026-10-18T12:00:00Z",
        "openCheck": {
            "checkId": str(CHECK_ID),
            "proposedBand": "low",
            "openedAt": "2026-10-20T00:00:00Z",
        },
    }


def test_us5_low_segment_has_no_rule_or_check() -> None:
    response = client(SegmentView("m-200", "low", None, 1, NOW, None)).get(
        "/merchants/m-200/segment"
    )

    assert response.status_code == 200
    assert (response.json()["rule"], response.json()["openCheck"]) == (None, None)


def test_us5_unknown_merchant_404() -> None:
    assert client().get("/merchants/m-999/segment").status_code == 404
