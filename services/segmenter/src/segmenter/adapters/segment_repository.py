from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import (
    ColumnElement,
    Connection,
    ScalarSelect,
    Select,
    and_,
    delete,
    func,
    or_,
    select,
    true,
    update,
)
from sqlalchemy.dialects.postgresql import insert

from segmenter.adapters.tables import facts, segments
from segmenter.domain.errors import VersionConflict
from segmenter.domain.policy import Band, Policy
from segmenter.domain.segment import Fact, FactCounts, Segment


class SegmentRepository(ABC):
    def __init__(self) -> None:
        self.seen: list[Segment] = []

    def get(self, merchant_id: str, policy: Policy) -> Segment:
        segment = self._load(merchant_id, policy)
        self.seen.append(segment)
        return segment

    def save(self, segment: Segment, policy: Policy) -> None:
        if not self._update(segment):
            raise VersionConflict(
                f"segment {segment.merchant_id} changed since version {segment.version_number}"
            )
        segment.version_number += 1
        if segment.fact_counts.anchor is not None:
            self._prune(segment.merchant_id, policy, segment.fact_counts.anchor)

    @abstractmethod
    def add_fact(self, fact: Fact, policy: Policy, at: datetime) -> bool: ...

    @abstractmethod
    def _load(self, merchant_id: str, policy: Policy) -> Segment: ...

    @abstractmethod
    def _update(self, segment: Segment) -> bool: ...

    @abstractmethod
    def _prune(self, merchant_id: str, policy: Policy, anchor: datetime) -> int: ...


class SqlSegmentRepository(SegmentRepository):
    def __init__(self, connection: Connection) -> None:
        super().__init__()
        self._connection = connection

    def add_fact(self, fact: Fact, policy: Policy, at: datetime) -> bool:
        # I5: two first events for one merchant meet on the primary key; the second waits, then
        # finds the row.
        self._connection.execute(
            insert(segments)
            .values(
                merchant_id=fact.merchant_id,
                band=Band.LOW.value,
                rule=None,
                policy_version=policy.version,
                version_number=0,
                updated_at=at,
            )
            .on_conflict_do_nothing(index_elements=["merchant_id"])
        )
        inserted = self._connection.execute(
            insert(facts)
            .values(
                activity_id=fact.activity_id,
                merchant_id=fact.merchant_id,
                type=fact.type,
                occurred_at=fact.occurred_at,
            )
            .on_conflict_do_nothing(index_elements=["activity_id"])
            .returning(facts.c.activity_id)
        )
        return inserted.first() is not None

    def _load(self, merchant_id: str, policy: Policy) -> Segment:
        # The segment and its counts come from one statement, so they share one snapshot. Read
        # apart, a fact committed between the two reads could be missed under a version number
        # that already includes it, and the version check would not catch it (I6).
        counts = self._counts_query(merchant_id, policy).subquery()
        row = (
            self._connection.execute(
                select(segments, counts)
                .select_from(segments.join(counts, true()))
                .where(segments.c.merchant_id == merchant_id)
            )
            .mappings()
            .one()
        )
        return Segment(
            merchant_id=row["merchant_id"],
            band=Band(row["band"]),
            rule=row["rule"],
            policy_version=row["policy_version"],
            version_number=row["version_number"],
            updated_at=row["updated_at"],
            fact_counts=FactCounts(
                anchor=row["anchor"],
                per_rule={rule.name: row[rule.name] for rule in policy.rules},
            ),
        )

    def _update(self, segment: Segment) -> bool:
        result = self._connection.execute(
            update(segments)
            .where(
                segments.c.merchant_id == segment.merchant_id,
                segments.c.version_number == segment.version_number,
            )
            .values(
                band=segment.band.value,
                rule=segment.rule,
                policy_version=segment.policy_version,
                version_number=segment.version_number + 1,
                updated_at=segment.updated_at,
            )
        )
        return result.rowcount == 1

    def _prune(self, merchant_id: str, policy: Policy, anchor: datetime) -> int:
        windows = policy.prune_windows()
        if not windows:
            return 0
        result = self._connection.execute(
            delete(facts).where(
                facts.c.merchant_id == merchant_id,
                or_(
                    *(
                        and_(facts.c.type == fact_type, facts.c.occurred_at < anchor - window)
                        for fact_type, window in windows.items()
                    )
                ),
            )
        )
        return result.rowcount

    def _counts_query(self, merchant_id: str, policy: Policy) -> Select[Any]:
        anchor = (
            select(func.max(facts.c.occurred_at))
            .where(facts.c.merchant_id == merchant_id)
            .scalar_subquery()
        )
        return select(
            anchor.label("anchor"),
            *(
                self._count(merchant_id, rule.type, anchor, rule.window).label(rule.name)
                for rule in policy.rules
            ),
        )

    @staticmethod
    def _count(
        merchant_id: str,
        fact_type: str,
        anchor: ScalarSelect[datetime],
        window: timedelta | None,
    ) -> ScalarSelect[int]:
        conditions: list[ColumnElement[bool]] = [
            facts.c.merchant_id == merchant_id,
            facts.c.type == fact_type,
        ]
        if window is not None:
            # P1: inclusive bound (#24). A seconds-only interval: a day-based one follows the
            # session time zone across a clock change.
            seconds = func.make_interval(0, 0, 0, 0, 0, 0, window.total_seconds())
            conditions.append(facts.c.occurred_at >= anchor - seconds)
        return select(func.count()).where(*conditions).scalar_subquery()
