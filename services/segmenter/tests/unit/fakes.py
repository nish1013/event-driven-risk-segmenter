import copy
from dataclasses import dataclass, field
from datetime import datetime
from typing import Self
from uuid import UUID

from segmenter.adapters.segment_repository import SegmentRepository
from segmenter.domain.check import Check
from segmenter.domain.events import SegmenterEvent
from segmenter.domain.policy import Band, Policy
from segmenter.domain.segment import Fact, FactCounts, Segment
from segmenter.service_layer.unit_of_work import UnitOfWork


@dataclass
class SegmentRow:
    band: Band
    rule: str | None
    policy_version: int
    version_number: int
    updated_at: datetime


@dataclass
class Store:
    segments: dict[str, SegmentRow] = field(default_factory=dict)
    facts: dict[str, Fact] = field(default_factory=dict)
    checks: dict[UUID, Check] = field(default_factory=dict)
    processed: set[UUID] = field(default_factory=set)
    outbox: list[SegmenterEvent] = field(default_factory=list)


class FakeSegmentRepository(SegmentRepository):
    def __init__(self, store: Store, conflicts: list[int]) -> None:
        super().__init__()
        self._store = store
        self._conflicts = conflicts

    def add_fact(self, fact: Fact, policy: Policy, at: datetime) -> bool:
        self._store.segments.setdefault(
            fact.merchant_id, SegmentRow(Band.LOW, None, policy.version, 0, at)
        )
        if fact.activity_id in self._store.facts:
            return False
        self._store.facts[fact.activity_id] = fact
        return True

    def _load(self, merchant_id: str, policy: Policy) -> Segment:
        row = self._store.segments[merchant_id]
        return Segment(
            merchant_id=merchant_id,
            band=row.band,
            rule=row.rule,
            policy_version=row.policy_version,
            version_number=row.version_number,
            updated_at=row.updated_at,
            fact_counts=self._count(merchant_id, policy),
            checks=[
                check
                for check in self._store.checks.values()
                if check.merchant_id == merchant_id and check.is_open
            ],
        )

    def _merchant_of(self, check_id: UUID) -> str | None:
        check = self._store.checks.get(check_id)
        return None if check is None else check.merchant_id

    def _update(self, segment: Segment) -> bool:
        if self._conflicts and self._conflicts[0] > 0:
            self._conflicts[0] -= 1
            return False
        row = self._store.segments[segment.merchant_id]
        if row.version_number != segment.version_number:
            return False
        self._store.segments[segment.merchant_id] = SegmentRow(
            segment.band,
            segment.rule,
            segment.policy_version,
            segment.version_number + 1,
            segment.updated_at,
        )
        return True

    def _insert_check(self, check: Check) -> bool:
        if any(
            other.merchant_id == check.merchant_id and other.is_open
            for other in self._store.checks.values()
        ):
            return False
        self._store.checks[check.check_id] = check
        return True

    def _update_open_check(self, check: Check) -> bool:
        if not self._store.checks[check.check_id].is_open:
            return False
        self._store.checks[check.check_id] = check
        return True

    def _prune(self, merchant_id: str, policy: Policy, anchor: datetime) -> int:
        windows = policy.prune_windows()
        old = [
            fact.activity_id
            for fact in self._store.facts.values()
            if fact.merchant_id == merchant_id
            and fact.type in windows
            and fact.occurred_at < anchor - windows[fact.type]
        ]
        for activity_id in old:
            del self._store.facts[activity_id]
        return len(old)

    def _count(self, merchant_id: str, policy: Policy) -> FactCounts:
        mine = [fact for fact in self._store.facts.values() if fact.merchant_id == merchant_id]
        if not mine:
            return FactCounts(None, {rule.name: 0 for rule in policy.rules})
        anchor = max(fact.occurred_at for fact in mine)
        return FactCounts(
            anchor,
            {
                rule.name: sum(
                    1
                    for fact in mine
                    if fact.type == rule.type
                    and (rule.window is None or fact.occurred_at >= anchor - rule.window)
                )
                for rule in policy.rules
            },
        )


class FakeUnitOfWork(UnitOfWork):
    """Works on a copy of the store; commit publishes the copy, so rollback undoes everything."""

    def __init__(self, store: Store, conflicts: list[int] | None = None) -> None:
        self.store = store
        self._conflicts = conflicts if conflicts is not None else []
        self._working = Store()

    def __enter__(self) -> Self:
        self._working = copy.deepcopy(self.store)
        self.segments = FakeSegmentRepository(self._working, self._conflicts)
        return super().__enter__()

    def mark_processed(self, event_id: UUID, at: datetime) -> bool:
        if event_id in self._working.processed:
            return False
        self._working.processed.add(event_id)
        return True

    def rollback(self) -> None:
        self._working = Store()

    def _add_to_outbox(self, event: SegmenterEvent) -> None:
        self._working.outbox.append(event)

    def _commit(self) -> None:
        self.store.__dict__.update(copy.deepcopy(self._working).__dict__)
