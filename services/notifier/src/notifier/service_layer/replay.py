from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ParkedMessage:
    ack_id: str
    data: bytes
    attributes: dict[str, str]
    ordering_key: str


class DeadLetters(Protocol):
    def pull(self) -> list[ParkedMessage]: ...

    def ack(self, message: ParkedMessage) -> None: ...

    def release(self, messages: list[ParkedMessage]) -> None: ...


class SourceTopic(Protocol):
    def republish(self, message: ParkedMessage) -> None: ...


@dataclass(frozen=True)
class Replayed:
    count: int
    parked_again: bool


def replay(dead_letters: DeadLetters, source: SourceTopic) -> Replayed:
    # #35: the message goes back unchanged, so a consumer that already applied it drops it by
    # eventId (#4). The dead-letter copy is acked only after the source topic acked the republish.
    replayed: set[bytes] = set()
    while batch := dead_letters.pull():
        for index, message in enumerate(batch):
            if message.data in replayed:
                # Parked again during this run, so the fault is not fixed. Stop instead of cycling.
                dead_letters.release(batch[index:])
                return Replayed(len(replayed), parked_again=True)
            try:
                source.republish(message)
            except Exception:
                dead_letters.release(batch[index:])
                raise
            dead_letters.ack(message)
            replayed.add(message.data)
    return Replayed(len(replayed), parked_again=False)
