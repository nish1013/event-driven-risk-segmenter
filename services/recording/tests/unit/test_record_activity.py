from itertools import count
from typing import Any
from uuid import UUID

import pytest
from fakes import FakeUnitOfWork
from pydantic import TypeAdapter
from samples import NOW, chargeback

from recording.domain.activity import Activity
from recording.domain.errors import ActivityConflict
from recording.domain.schemas import NewActivity
from recording.service_layer.record_activity import Recorded, record_activity

adapter: TypeAdapter[Any] = TypeAdapter(NewActivity)
ids = count(1)


def record(unit_of_work: FakeUnitOfWork, body: dict[str, Any]) -> Recorded:
    return record_activity(
        adapter.validate_python(body), unit_of_work, lambda: NOW, lambda: UUID(int=next(ids))
    )


def test_us1_new_activity_stored_with_one_event() -> None:
    unit_of_work = FakeUnitOfWork()

    recorded = record(unit_of_work, chargeback())

    assert recorded.created
    assert list(unit_of_work.activities.rows) == ["a-1"]
    assert [event.activity_id for event in unit_of_work.outbox] == ["a-1"]
    assert unit_of_work.outbox[0].payload == chargeback()["payload"]


def test_us1_same_body_returns_original_no_event() -> None:
    unit_of_work = FakeUnitOfWork()
    original = record(unit_of_work, chargeback()).activity

    repeat = record(unit_of_work, chargeback())

    assert not repeat.created
    assert repeat.activity is original
    assert len(unit_of_work.outbox) == 1


def test_us1_other_body_conflicts_nothing_changes() -> None:
    unit_of_work = FakeUnitOfWork()
    original = record(unit_of_work, chargeback()).activity

    with pytest.raises(ActivityConflict):
        record(
            unit_of_work, chargeback(type="failed_login", payload={"userId": "u", "method": "otp"})
        )

    assert unit_of_work.activities.rows == {"a-1": original}
    assert len(unit_of_work.outbox) == 1


def test_i1_insert_lost_to_concurrent_writer_returns_winner() -> None:
    unit_of_work = FakeUnitOfWork()
    winner = record(unit_of_work, chargeback()).activity
    unit_of_work.activities.hidden_from_get.add("a-1")

    loser = record(unit_of_work, chargeback())

    assert not loser.created
    assert loser.activity is winner
    assert len(unit_of_work.outbox) == 1


def test_i3_conflict_after_lost_race_writes_nothing() -> None:
    unit_of_work = FakeUnitOfWork()
    record(unit_of_work, chargeback())
    unit_of_work.activities.hidden_from_get.add("a-1")

    with pytest.raises(ActivityConflict):
        record(unit_of_work, chargeback(merchantId="m-200"))

    assert isinstance(unit_of_work.activities.rows["a-1"], Activity)
    assert unit_of_work.activities.rows["a-1"].merchant_id == "m-100"
    assert len(unit_of_work.outbox) == 1
