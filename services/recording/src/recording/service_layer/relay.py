from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from recording.adapters.outbox import OutboxMessage

BATCH_SIZE = 100
SENT_RETENTION = timedelta(days=7)


class Publisher(Protocol):
    def publish(self, messages: Sequence[OutboxMessage]) -> list[bool]: ...


class RelayOutbox(Protocol):
    def unsent(self, limit: int) -> list[OutboxMessage]: ...

    def mark_sent(self, ids: list[int], sent_at: datetime) -> None: ...

    def delete_sent_before(self, cutoff: datetime) -> int: ...


@dataclass(frozen=True)
class RelayResult:
    sent: int
    failed: bool


def relay_once(outbox: RelayOutbox, publisher: Publisher, now: datetime) -> RelayResult:
    batch = outbox.unsent(BATCH_SIZE)
    if not batch:
        return RelayResult(sent=0, failed=False)
    acks = publisher.publish(batch)
    # Only the acked prefix is marked: a row after the first failure stays unsent, so the next
    # run starts there and a merchant's events are never published past a gap (#3).
    sent: list[int] = []
    for message, acked in zip(batch, acks, strict=True):
        if not acked:
            break
        sent.append(message.id)
    outbox.mark_sent(sent, now)
    return RelayResult(sent=len(sent), failed=len(sent) < len(batch))


def clean_outbox(outbox: RelayOutbox, now: datetime) -> int:
    return outbox.delete_sent_before(now - SENT_RETENTION)
