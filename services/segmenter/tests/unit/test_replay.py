import pytest

from segmenter.service_layer.replay import ParkedMessage, replay


def parked(n: int) -> ParkedMessage:
    return ParkedMessage(
        ack_id=f"ack-{n}",
        data=f'{{"eventId":"e-{n}"}}'.encode(),
        attributes={"eventId": f"e-{n}", "eventType": "ActivityRecorded"},
        ordering_key="m-200",
    )


class FakeDeadLetters:
    def __init__(self, *batches: list[ParkedMessage]) -> None:
        self.batches = list(batches)
        self.log: list[str] = []
        self.released: list[str] = []

    def pull(self) -> list[ParkedMessage]:
        return self.batches.pop(0) if self.batches else []

    def ack(self, message: ParkedMessage) -> None:
        self.log.append(f"ack {message.ack_id}")

    def release(self, messages: list[ParkedMessage]) -> None:
        self.released += [message.ack_id for message in messages]


class FakeSourceTopic:
    def __init__(self, log: list[str], failing: set[str] | None = None) -> None:
        self.log = log
        self.failing = failing or set()
        self.published: list[ParkedMessage] = []

    def republish(self, message: ParkedMessage) -> None:
        if message.ack_id in self.failing:
            raise RuntimeError("publish not acked")
        self.published.append(message)
        self.log.append(f"publish {message.ack_id}")


def test_us10_35_republished_unchanged_then_acked() -> None:
    dead_letters = FakeDeadLetters([parked(1), parked(2)], [parked(3)])
    source = FakeSourceTopic(dead_letters.log)

    result = replay(dead_letters, source)

    assert result.count == 3
    assert source.published == [parked(1), parked(2), parked(3)]
    assert dead_letters.log == [
        "publish ack-1",
        "ack ack-1",
        "publish ack-2",
        "ack ack-2",
        "publish ack-3",
        "ack ack-3",
    ]


def test_us10_empty_dead_letter_replays_nothing() -> None:
    result = replay(FakeDeadLetters(), FakeSourceTopic([]))

    assert (result.count, result.parked_again) == (0, False)


def test_35_unacked_republish_keeps_message_parked() -> None:
    dead_letters = FakeDeadLetters([parked(1), parked(2), parked(3)])

    with pytest.raises(RuntimeError):
        replay(dead_letters, FakeSourceTopic(dead_letters.log, failing={"ack-2"}))

    assert dead_letters.log == ["publish ack-1", "ack ack-1"]
    assert dead_letters.released == ["ack-2", "ack-3"]


def test_10_message_parked_again_stops_replay() -> None:
    again = ParkedMessage("ack-9", parked(1).data, parked(1).attributes, "m-200")
    dead_letters = FakeDeadLetters([parked(1)], [again, parked(2)])
    source = FakeSourceTopic(dead_letters.log)

    result = replay(dead_letters, source)

    assert (result.count, result.parked_again) == (1, True)
    assert source.published == [parked(1)]
    assert dead_letters.released == ["ack-9", "ack-2"]
