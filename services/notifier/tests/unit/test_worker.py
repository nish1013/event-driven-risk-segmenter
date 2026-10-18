import json
from uuid import UUID

import pytest
from samples import at

from notifier.domain.events import (
    Decision,
    ManualRiskCheckRequired,
    RiskCheckResolved,
    RiskSegmentChanged,
    SegmenterEvent,
)
from notifier.entrypoints.worker import handle

EVENT_ID = UUID(int=7)
CHECK_ID = UUID(int=8)
COMMON = {
    "eventId": str(EVENT_ID),
    "merchantId": "m-200",
    "occurredAt": "2026-10-20T10:00:00+00:00",
}
CHANGED = COMMON | {
    "eventType": "RiskSegmentChanged",
    "from": "low",
    "to": "medium",
    "rule": "chargeback_1_in_90d",
    "policyVersion": 1,
}
OPENED = COMMON | {
    "eventType": "ManualRiskCheckRequired",
    "checkId": str(CHECK_ID),
    "currentBand": "high",
    "proposedBand": "low",
    "policyVersion": 1,
}
RESOLVED = COMMON | {
    "eventType": "RiskCheckResolved",
    "checkId": str(CHECK_ID),
    "decision": "superseded",
    "officerId": None,
    "reason": None,
}
WHEN = at("2026-10-20T10:00")


class FakeMessage:
    def __init__(self, body: dict[str, object] | bytes) -> None:
        self.data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.acked = False
        self.nacked = False

    def ack(self) -> None:
        self.acked = True

    def nack(self) -> None:
        self.nacked = True


@pytest.mark.parametrize(
    ("body", "event"),
    [
        (
            CHANGED,
            RiskSegmentChanged(EVENT_ID, "m-200", WHEN, "low", "medium", "chargeback_1_in_90d", 1),
        ),
        (OPENED, ManualRiskCheckRequired(EVENT_ID, "m-200", WHEN, CHECK_ID, "high", "low", 1)),
        (
            RESOLVED,
            RiskCheckResolved(EVENT_ID, "m-200", WHEN, CHECK_ID, Decision.SUPERSEDED, None, None),
        ),
    ],
)
def test_us6_us8_each_event_type_applied_then_acked(
    body: dict[str, object], event: SegmenterEvent
) -> None:
    message = FakeMessage(body)
    seen: list[SegmenterEvent] = []

    handle(message, seen.append)

    assert seen == [event]
    assert (message.acked, message.nacked) == (True, False)


def test_29_other_event_type_acked_not_processed() -> None:
    message = FakeMessage(COMMON | {"eventType": "MerchantSuspended"})
    seen: list[SegmenterEvent] = []

    handle(message, seen.append)

    assert (message.acked, seen) == (True, [])


def test_5_unknown_field_ignored() -> None:
    message = FakeMessage(CHANGED | {"newField": "x"})

    handle(message, lambda _: None)

    assert message.acked


def test_10_failure_nacks_for_redelivery() -> None:
    message = FakeMessage(OPENED)

    def fail(_: SegmenterEvent) -> None:
        raise RuntimeError("database down")

    handle(message, fail)

    assert (message.acked, message.nacked) == (False, True)


@pytest.mark.parametrize(
    "body",
    [
        b"not json",
        {"eventId": str(EVENT_ID)},
        RESOLVED | {"decision": "approve"},
        OPENED | {"checkId": "c-1"},
    ],
)
def test_10_unreadable_message_nacked_not_processed(body: dict[str, object] | bytes) -> None:
    message = FakeMessage(body)
    seen: list[SegmenterEvent] = []

    handle(message, seen.append)

    assert (message.nacked, seen) == (True, [])
