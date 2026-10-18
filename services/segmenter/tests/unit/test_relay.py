from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from uuid import UUID

from segmenter.adapters.outbox import OutboxMessage
from segmenter.service_layer.relay import clean_outbox, relay_once

NOW = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)


class FakeOutbox:
    def __init__(self, count: int) -> None:
        self.rows = {
            i: OutboxMessage(i, UUID(int=i), "segmenter-events", f"m-{i % 2}", {"eventId": i})
            for i in range(1, count + 1)
        }
        self.sent: dict[int, datetime] = {}
        self.cutoff: datetime | None = None

    def unsent(self, limit: int) -> list[OutboxMessage]:
        return [row for i, row in sorted(self.rows.items()) if i not in self.sent][:limit]

    def mark_sent(self, ids: list[int], sent_at: datetime) -> None:
        self.sent.update(dict.fromkeys(ids, sent_at))

    def delete_sent_before(self, cutoff: datetime) -> int:
        self.cutoff = cutoff
        return 0


class FakePublisher:
    def __init__(self, failing: set[int] | None = None) -> None:
        self.failing = failing or set()
        self.published: list[int] = []

    def publish(self, messages: Sequence[OutboxMessage]) -> list[bool]:
        self.published.extend(message.id for message in messages)
        return [message.id not in self.failing for message in messages]


def test_i4_all_acked_rows_marked_sent() -> None:
    outbox = FakeOutbox(3)

    result = relay_once(outbox, FakePublisher(), NOW)

    assert (result.sent, result.failed) == (3, False)
    assert outbox.sent == dict.fromkeys([1, 2, 3], NOW)


def test_3_rows_after_first_failure_stay_unsent() -> None:
    outbox = FakeOutbox(4)

    result = relay_once(outbox, FakePublisher(failing={2}), NOW)

    assert (result.sent, result.failed) == (1, True)
    assert list(outbox.sent) == [1]


def test_i4_next_run_retries_from_first_unsent() -> None:
    outbox = FakeOutbox(4)
    relay_once(outbox, FakePublisher(failing={2}), NOW)
    publisher = FakePublisher()

    relay_once(outbox, publisher, NOW)

    assert publisher.published == [2, 3, 4]
    assert list(outbox.sent) == [1, 2, 3, 4]


def test_3_empty_outbox_publishes_nothing() -> None:
    publisher = FakePublisher()

    result = relay_once(FakeOutbox(0), publisher, NOW)

    assert (result.sent, result.failed, publisher.published) == (0, False, [])


def test_40_cleanup_cutoff_is_seven_days() -> None:
    outbox = FakeOutbox(0)

    clean_outbox(outbox, NOW)

    assert outbox.cutoff == NOW - timedelta(days=7)
