import json
from uuid import UUID

from samples import at

from segmenter.domain.segment import Fact
from segmenter.entrypoints.worker import handle
from segmenter.service_layer.on_activity_recorded import ActivityRecorded

EVENT_ID = UUID(int=7)


class FakeMessage:
    def __init__(self, body: dict[str, object] | bytes) -> None:
        self.data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.acked = False
        self.nacked = False

    def ack(self) -> None:
        self.acked = True

    def nack(self) -> None:
        self.nacked = True


def body(**overrides: object) -> dict[str, object]:
    return {
        "eventId": str(EVENT_ID),
        "eventType": "ActivityRecorded",
        "activityId": "a-1",
        "merchantId": "m-100",
        "type": "chargeback",
        "occurredAt": "2026-10-01T10:00:00+00:00",
        "payload": {"transactionId": "t-9"},
        "recordedAt": "2026-10-01T10:00:01+00:00",
    } | overrides


def test_us2_applied_event_acked_after_processing() -> None:
    message = FakeMessage(body())
    seen: list[ActivityRecorded] = []

    handle(message, seen.append)

    assert seen == [
        ActivityRecorded(EVENT_ID, Fact("a-1", "m-100", "chargeback", at("2026-10-01T10:00")))
    ]
    assert (message.acked, message.nacked) == (True, False)


def test_5_unknown_field_ignored() -> None:
    message = FakeMessage(body(newField="x"))

    handle(message, lambda _: None)

    assert message.acked


def test_10_failure_nacks_for_redelivery() -> None:
    message = FakeMessage(body())

    def fail(_: ActivityRecorded) -> None:
        raise RuntimeError("database down")

    handle(message, fail)

    assert (message.acked, message.nacked) == (False, True)


def test_10_unreadable_message_nacked_not_processed() -> None:
    for message in [FakeMessage(b"not json"), FakeMessage(body(type="refund"))]:
        seen: list[ActivityRecorded] = []

        handle(message, seen.append)

        assert (message.nacked, seen) == (True, [])
