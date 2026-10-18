from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

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
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import IntegrityError

from segmenter.adapters.tables import checks, facts, segments
from segmenter.domain.check import Check, CheckStatus, Decision
from segmenter.domain.errors import VersionConflict
from segmenter.domain.policy import Band, Policy
from segmenter.domain.segment import Fact, FactCounts, Segment

ONE_OPEN_CHECK_INDEX = "checks_one_open_per_merchant"


class SegmentRepository(ABC):
    def __init__(self) -> None:
        self.seen: list[Segment] = []
        self._loaded_checks: dict[str, dict[UUID, Check]] = {}

    def get(self, merchant_id: str, policy: Policy) -> Segment:
        segment = self._load(merchant_id, policy)
        self.seen.append(segment)
        self._loaded_checks[merchant_id] = {check.check_id: check for check in segment.checks}
        return segment

    def get_by_check(self, check_id: UUID, policy: Policy) -> Segment | None:
        merchant_id = self._merchant_of(check_id)
        if merchant_id is None:
            return None
        return self.get(merchant_id, policy)

    def save(self, segment: Segment, policy: Policy) -> None:
        segment.assert_invariants()
        conflict = VersionConflict(
            f"segment {segment.merchant_id} changed since version {segment.version_number}"
        )
        if not self._update(segment):
            raise conflict
        loaded = self._loaded_checks.get(segment.merchant_id, {})
        for check in segment.checks:
            before = loaded.get(check.check_id)
            if before is None:
                written = self._insert_check(check)
            elif check != before:
                written = self._update_open_check(check)
            else:
                continue
            if not written:
                raise conflict
        segment.version_number += 1
        self._loaded_checks[segment.merchant_id] = {c.check_id: c for c in segment.checks}
        if segment.fact_counts.anchor is not None:
            self._prune(segment.merchant_id, policy, segment.fact_counts.anchor)

    @abstractmethod
    def add_fact(self, fact: Fact, policy: Policy, at: datetime) -> bool: ...

    @abstractmethod
    def _load(self, merchant_id: str, policy: Policy) -> Segment: ...

    @abstractmethod
    def _merchant_of(self, check_id: UUID) -> str | None: ...

    @abstractmethod
    def _update(self, segment: Segment) -> bool: ...

    @abstractmethod
    def _insert_check(self, check: Check) -> bool: ...

    @abstractmethod
    def _update_open_check(self, check: Check) -> bool: ...

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
        # The open check joins the same statement for the same reason.
        counts = self._counts_query(merchant_id, policy).subquery()
        open_check = checks.alias("open_check")
        row = (
            self._connection.execute(
                select(
                    segments,
                    counts,
                    *(column.label(f"check_{column.name}") for column in open_check.c),
                )
                .select_from(
                    segments.join(counts, true()).outerjoin(
                        open_check,
                        and_(
                            open_check.c.merchant_id == segments.c.merchant_id,
                            open_check.c.status == CheckStatus.OPEN.value,
                        ),
                    )
                )
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
            checks=[] if row["check_check_id"] is None else [self._open_check(row)],
        )

    def _merchant_of(self, check_id: UUID) -> str | None:
        return self._connection.scalar(
            select(checks.c.merchant_id).where(checks.c.check_id == check_id)
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

    def _insert_check(self, check: Check) -> bool:
        # I7: a second open check for the merchant meets the partial unique index.
        try:
            self._connection.execute(checks.insert().values(**self._check_values(check)))
        except IntegrityError as error:
            diag = getattr(error.orig, "diag", None)
            if diag is None or diag.constraint_name != ONE_OPEN_CHECK_INDEX:
                raise
            return False
        return True

    def _update_open_check(self, check: Check) -> bool:
        # I9: only an open check is written, so a resolved check never changes.
        result = self._connection.execute(
            update(checks)
            .where(checks.c.check_id == check.check_id, checks.c.status == CheckStatus.OPEN.value)
            .values(**self._check_values(check))
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

    @staticmethod
    def _open_check(row: RowMapping) -> Check:
        decision = row["check_decision"]
        return Check(
            check_id=row["check_check_id"],
            merchant_id=row["check_merchant_id"],
            proposed_band=Band(row["check_proposed_band"]),
            status=CheckStatus(row["check_status"]),
            decision=None if decision is None else Decision(decision),
            officer_id=row["check_officer_id"],
            reason=row["check_reason"],
            opened_at=row["check_opened_at"],
            resolved_at=row["check_resolved_at"],
        )

    @staticmethod
    def _check_values(check: Check) -> dict[str, object]:
        return {
            "check_id": check.check_id,
            "merchant_id": check.merchant_id,
            "proposed_band": check.proposed_band.value,
            "status": check.status.value,
            "decision": None if check.decision is None else check.decision.value,
            "officer_id": check.officer_id,
            "reason": check.reason,
            "opened_at": check.opened_at,
            "resolved_at": check.resolved_at,
        }
