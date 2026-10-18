import json
from datetime import timedelta
from typing import Any
from uuid import uuid4

import pytest
from pydantic import TypeAdapter
from samples import NOW, chargeback

from recording.domain.activity import Activity
from recording.domain.errors import OccurredAtInFuture
from recording.domain.schemas import NewActivity

adapter: TypeAdapter[Any] = TypeAdapter(NewActivity)


def record(body: dict[str, Any]) -> Activity:
    return Activity.record(adapter.validate_python(body), recorded_at=NOW, event_id=uuid4())


COMPACT = (
    '{"activityId":"a-1","merchantId":"m-100","type":"chargeback",'
    '"occurredAt":"2026-10-01T10:00:00Z","payload":{"transactionId":"t-9",'
    '"amount":"120.00","currency":"GBP","reasonCode":"4837"}}'
)
REORDERED = """
{
    "payload": {"reasonCode": "4837", "currency": "GBP",
                "amount": "120.00", "transactionId": "t-9"},
    "occurredAt": "2026-10-01T10:00:00Z",
    "type":  "chargeback",
    "merchantId": "m-100",
    "activityId": "a-1"
}
"""


def test_26_same_body_ignores_key_order_and_whitespace() -> None:
    assert record(json.loads(COMPACT)).same_body(record(json.loads(REORDERED)))


def test_26_same_body_same_instant_other_offset() -> None:
    assert record(chargeback()).same_body(
        record(chargeback(occurredAt="2026-10-01T11:00:00+01:00"))
    )


@pytest.mark.parametrize(
    "changed",
    [
        {"payload": chargeback()["payload"] | {"amount": "121.00"}},
        {"merchantId": "m-200"},
        {"occurredAt": "2026-10-01T10:00:01Z"},
        {"type": "failed_login", "payload": {"userId": "u-1", "method": "password"}},
    ],
)
def test_26_same_body_detects_change(changed: dict[str, Any]) -> None:
    assert not record(chargeback()).same_body(record(chargeback(**changed)))


def test_31_occurred_at_five_minutes_ahead_accepted() -> None:
    record(chargeback(occurredAt=(NOW + timedelta(minutes=5)).isoformat()))


def test_31_us1_occurred_at_six_minutes_ahead_refused() -> None:
    with pytest.raises(OccurredAtInFuture):
        record(chargeback(occurredAt="2026-10-01T10:06:00Z"))


def test_31_occurred_at_one_second_past_skew_refused() -> None:
    with pytest.raises(OccurredAtInFuture):
        record(chargeback(occurredAt=(NOW + timedelta(minutes=5, seconds=1)).isoformat()))


def test_us1_record_raises_activity_recorded_with_same_fields() -> None:
    event_id = uuid4()
    activity = Activity.record(
        adapter.validate_python(chargeback()), recorded_at=NOW, event_id=event_id
    )

    assert [event.body() for event in activity.events] == [
        {
            "eventId": str(event_id),
            "eventType": "ActivityRecorded",
            "activityId": "a-1",
            "merchantId": "m-100",
            "type": "chargeback",
            "occurredAt": "2026-10-01T10:00:00+00:00",
            "payload": chargeback()["payload"],
            "recordedAt": NOW.isoformat(),
        }
    ]
