from typing import Any

import pytest
from pydantic import TypeAdapter, ValidationError
from samples import chargeback

from recording.domain.schemas import NewActivity

adapter: TypeAdapter[Any] = TypeAdapter(NewActivity)

VALID = {
    "chargeback": chargeback()["payload"],
    "velocity_breach": {"metric": "txn_count", "window": "P7D", "observed": "12", "limit": "10"},
    "kyc_expired": {"kycCaseId": "k-1", "documentType": "identity"},
    "failed_login": {"userId": "u-1", "method": "password"},
}


def body(activity_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    return chargeback(type=activity_type, payload=payload)


@pytest.mark.parametrize("activity_type", VALID)
def test_30_each_type_valid(activity_type: str) -> None:
    parsed = adapter.validate_python(body(activity_type, VALID[activity_type]))

    assert parsed.type == activity_type
    assert parsed.payload.model_dump(by_alias=True) == VALID[activity_type]


@pytest.mark.parametrize(
    ("activity_type", "field"),
    [(activity_type, field) for activity_type, payload in VALID.items() for field in payload],
)
def test_30_each_payload_field_required(activity_type: str, field: str) -> None:
    payload = {key: value for key, value in VALID[activity_type].items() if key != field}

    with pytest.raises(ValidationError):
        adapter.validate_python(body(activity_type, payload))


@pytest.mark.parametrize("field", ["activityId", "merchantId", "type", "occurredAt", "payload"])
def test_us1_top_level_field_required(field: str) -> None:
    incomplete = {key: value for key, value in chargeback().items() if key != field}

    with pytest.raises(ValidationError):
        adapter.validate_python(incomplete)


@pytest.mark.parametrize("activity_type", VALID)
def test_30_unknown_payload_field(activity_type: str) -> None:
    with pytest.raises(ValidationError):
        adapter.validate_python(body(activity_type, {**VALID[activity_type], "ip": "10.0.0.1"}))


def test_30_unknown_top_level_field() -> None:
    with pytest.raises(ValidationError):
        adapter.validate_python(chargeback(email="x@example.com"))


@pytest.mark.parametrize(
    ("activity_type", "field", "value"),
    [
        ("velocity_breach", "metric", "txn_rate"),
        ("kyc_expired", "documentType", "passport"),
        ("failed_login", "method", "sms"),
    ],
)
def test_30_bad_enum_value(activity_type: str, field: str, value: str) -> None:
    with pytest.raises(ValidationError):
        adapter.validate_python(body(activity_type, {**VALID[activity_type], field: value}))


def test_us1_unknown_type_refund() -> None:
    with pytest.raises(ValidationError):
        adapter.validate_python(body("refund", VALID["chargeback"]))


@pytest.mark.parametrize(
    ("activity_type", "field", "value"),
    [
        ("chargeback", "amount", "12,00"),
        ("chargeback", "amount", 120),
        ("chargeback", "amount", "-1.00"),
        ("chargeback", "currency", "gbp"),
        ("chargeback", "transactionId", ""),
        ("velocity_breach", "window", "7 days"),
        ("velocity_breach", "window", "PT"),
        ("velocity_breach", "limit", "ten"),
    ],
)
def test_30_bad_field_format(activity_type: str, field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        adapter.validate_python(body(activity_type, {**VALID[activity_type], field: value}))


def test_us1_occurred_at_needs_offset() -> None:
    with pytest.raises(ValidationError):
        adapter.validate_python(chargeback(occurredAt="2026-10-01T10:00:00"))
