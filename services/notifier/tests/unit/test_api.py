from fastapi.testclient import TestClient
from samples import MERCHANT, changed, opened
from test_healthz import FakeProbe

from notifier.domain.alert import Alert, AlertStatus
from notifier.entrypoints.api import create_app


class FakeQueries:
    def __init__(self, *alerts: Alert) -> None:
        self.calls: list[tuple[str, AlertStatus | None]] = []
        self._alerts = alerts

    def alerts(self, merchant_id: str, status: AlertStatus | None) -> list[Alert]:
        self.calls.append((merchant_id, status))
        return [
            alert
            for alert in self._alerts
            if alert.merchant_id == merchant_id and status in (None, alert.status)
        ]


def client(queries: FakeQueries) -> TestClient:
    return TestClient(create_app(FakeProbe(reachable=True), queries))


def test_us9_alerts_listed_with_kind_and_status() -> None:
    check = opened()
    queries = FakeQueries(Alert.of(check, check.event_id))

    response = client(queries).get(f"/merchants/{MERCHANT}/alerts")

    assert response.status_code == 200
    [alert] = response.json()
    assert (alert["kind"], alert["status"], alert["checkId"]) == (
        "risk_check_required",
        "open",
        str(check.check_id),
    )


def test_us9_status_filter_passed_through() -> None:
    change, check = changed("low", "medium"), opened()
    queries = FakeQueries(Alert.of(change, change.event_id), Alert.of(check, check.event_id))

    response = client(queries).get(f"/merchants/{MERCHANT}/alerts", params={"status": "open"})

    assert [a["kind"] for a in response.json()] == ["risk_check_required"]
    assert queries.calls == [(MERCHANT, AlertStatus.OPEN)]


def test_us9_unknown_merchant_empty_list() -> None:
    response = client(FakeQueries()).get("/merchants/m-999/alerts")

    assert (response.status_code, response.json()) == (200, [])


def test_us9_unknown_status_422() -> None:
    response = client(FakeQueries()).get(f"/merchants/{MERCHANT}/alerts?status=pending")

    assert response.status_code == 422
