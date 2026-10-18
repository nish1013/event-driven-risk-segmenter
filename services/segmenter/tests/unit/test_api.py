from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from samples import NOW, at
from test_healthz import FakeProbe, no_checks

from segmenter.adapters.segment_queries import OpenCheckView, SegmentView
from segmenter.domain.check import Check, Decision
from segmenter.domain.errors import VersionConflict
from segmenter.domain.policy import Band
from segmenter.entrypoints.api import Resolver, create_app
from segmenter.service_layer.resolve_risk_check import Outcome, Resolution, ResolveRiskCheck

CHECK_ID = UUID(int=1)
RESOLVE = f"/risk-checks/{CHECK_ID}/resolution"
BODY = {"decision": "approve_lower", "officerId": "o-7", "reason": "chargebacks settled"}


class FakeQueries:
    def __init__(self, *views: SegmentView) -> None:
        self._views = {view.merchant_id: view for view in views}

    def segment(self, merchant_id: str) -> SegmentView | None:
        return self._views.get(merchant_id)


class FakeResolver:
    def __init__(self, outcome: Outcome | None = None) -> None:
        self.outcome = outcome
        self.commands: list[ResolveRiskCheck] = []

    def __call__(self, command: ResolveRiskCheck) -> Resolution:
        self.commands.append(command)
        if self.outcome is None:
            raise VersionConflict("busy")
        if self.outcome is not Outcome.RESOLVED:
            return Resolution(self.outcome)
        check = Check.open(command.check_id, "m-100", Band.LOW, at("2026-10-20T00:00")).resolve(
            command.decision, command.officer_id, command.reason, NOW
        )
        return Resolution(Outcome.RESOLVED, check, Band.LOW)


def client(*views: SegmentView, resolve: Resolver = no_checks) -> TestClient:
    return TestClient(create_app(FakeProbe(reachable=True), FakeQueries(*views), resolve))


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


def test_us4_resolve_200_with_check() -> None:
    resolver = FakeResolver(Outcome.RESOLVED)

    response = client(resolve=resolver).post(RESOLVE, json=BODY)

    assert response.status_code == 200
    assert response.json() == {
        "checkId": str(CHECK_ID),
        "merchantId": "m-100",
        "status": "resolved",
        "decision": "approve_lower",
        "officerId": "o-7",
        "reason": "chargebacks settled",
        "resolvedAt": "2026-10-18T12:00:00Z",
        "band": "low",
    }
    assert resolver.commands == [
        ResolveRiskCheck(CHECK_ID, Decision.APPROVE_LOWER, "o-7", "chargebacks settled")
    ]


@pytest.mark.parametrize(
    ("outcome", "code"),
    [(Outcome.NOT_FOUND, 404), (Outcome.NOT_OPEN, 409), (None, 503)],
)
def test_us4_a4_resolve_error_codes(outcome: Outcome | None, code: int) -> None:
    assert client(resolve=FakeResolver(outcome)).post(RESOLVE, json=BODY).status_code == code


def test_us4_check_id_not_a_uuid_404() -> None:
    resolver = FakeResolver(Outcome.RESOLVED)

    response = client(resolve=resolver).post("/risk-checks/c-9/resolution", json=BODY)

    assert (response.status_code, resolver.commands) == (404, [])


@pytest.mark.parametrize(
    "body",
    [
        {"officerId": "o-7", "reason": "r"},
        {"decision": "approve_lower", "reason": "r"},
        {"decision": "approve_lower", "officerId": "o-7"},
        BODY | {"reason": "  "},
        BODY | {"decision": "superseded"},
        BODY | {"decision": "lower"},
        BODY | {"note": "x"},
    ],
    ids=["no-decision", "no-officer", "no-reason", "blank-reason", "superseded", "bad", "extra"],
)
def test_us4_resolve_bad_body_422(body: dict[str, Any]) -> None:
    resolver = FakeResolver(Outcome.RESOLVED)

    response = client(resolve=resolver).post(RESOLVE, json=body)

    assert (response.status_code, resolver.commands) == (422, [])
