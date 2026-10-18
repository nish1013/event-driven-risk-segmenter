from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from segmenter_db import at
from sqlalchemy import Connection, event, text
from sqlalchemy.exc import ProgrammingError

from segmenter.adapters.segment_repository import SqlSegmentRepository
from segmenter.domain.policy import POLICY_V1
from segmenter.domain.segment import Fact

ANCHOR = at("2026-10-20T00:00")


def add(repository: SqlSegmentRepository, merchant: str, fact_type: str, age: timedelta) -> None:
    repository.add_fact(
        Fact(f"a-{merchant}-{fact_type}-{age}", merchant, fact_type, ANCHOR - age),
        POLICY_V1,
        datetime.now(UTC),
    )


def test_39_counts_at_each_window_boundary(connection: Connection, merchant: str) -> None:
    repository = SqlSegmentRepository(connection)
    for fact_type, window in [
        ("chargeback", timedelta(days=30)),
        ("velocity_breach", timedelta(days=7)),
        ("failed_login", timedelta(hours=1)),
    ]:
        add(repository, merchant, fact_type, window)
        add(repository, merchant, fact_type, window + timedelta(seconds=1))
    add(repository, merchant, "failed_login", timedelta(0))
    add(repository, merchant, "kyc_expired", timedelta(days=3000))

    counts = repository.get(merchant, POLICY_V1).fact_counts

    assert counts.anchor == ANCHOR
    assert counts.per_rule == {
        "kyc_expired": 1,
        "chargeback_3_in_30d": 1,
        "chargeback_1_in_30d": 1,
        "velocity_breach_2_in_7d": 1,
        "failed_login_10_in_1h": 2,
    }


def test_38_count_query_uses_index_for_hot_merchant(connection: Connection, merchant: str) -> None:
    repository = SqlSegmentRepository(connection)
    add(repository, merchant, "chargeback", timedelta(days=1))
    connection.execute(
        text(
            "INSERT INTO segmenter.facts (activity_id, merchant_id, type, occurred_at) "
            "SELECT :m || '-fl-' || n, :m, 'failed_login', :anchor - n * interval '1 second' "
            "FROM generate_series(1, 10000) AS n"
        ),
        {"m": merchant, "anchor": ANCHOR},
    )
    captured: list[tuple[str, Any]] = []

    def capture(*args: Any) -> None:
        captured.append((args[2], args[3]))

    event.listen(connection, "before_cursor_execute", capture)
    repository.get(merchant, POLICY_V1)
    event.remove(connection, "before_cursor_execute", capture)
    statement, parameters = captured[-1]
    plan = "\n".join(
        row[0] for row in connection.exec_driver_sql(f"EXPLAIN {statement}", parameters)
    )

    assert "facts_merchant_type_occurred" in plan, plan
    assert "Seq Scan on facts" not in plan, plan


@pytest.mark.parametrize(
    "statement", ["DELETE FROM segmenter.segments", "TRUNCATE segmenter.facts"]
)
def test_36_role_cannot_wipe_segments(connection: Connection, statement: str) -> None:
    with pytest.raises(ProgrammingError, match="permission denied"):
        connection.execute(text(statement))
