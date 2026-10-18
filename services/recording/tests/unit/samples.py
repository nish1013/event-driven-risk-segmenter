from datetime import UTC, datetime
from typing import Any

NOW = datetime(2026, 10, 1, 10, 0, tzinfo=UTC)


def chargeback(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "activityId": "a-1",
        "merchantId": "m-100",
        "type": "chargeback",
        "occurredAt": "2026-10-01T10:00:00Z",
        "payload": {
            "transactionId": "t-9",
            "amount": "120.00",
            "currency": "GBP",
            "reasonCode": "4837",
        },
    }
    body.update(overrides)
    return body
