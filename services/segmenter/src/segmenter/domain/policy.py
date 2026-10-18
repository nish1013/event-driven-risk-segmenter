from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum


class Band(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"

    @property
    def rank(self) -> int:
        return list(Band).index(self)


@dataclass(frozen=True)
class Rule:
    name: str
    type: str
    count: int
    window: timedelta | None
    band: Band


@dataclass(frozen=True)
class RuleResult:
    band: Band
    rule: str | None


@dataclass(frozen=True)
class Policy:
    version: int
    rules: tuple[Rule, ...]

    def rule_band(self, counts: Mapping[str, int]) -> RuleResult:
        # P3: top-down, the first rule that fires wins.
        for rule in self.rules:
            if counts.get(rule.name, 0) >= rule.count:
                return RuleResult(rule.band, rule.name)
        return RuleResult(Band.LOW, None)

    def prune_windows(self) -> dict[str, timedelta]:
        # P6: how long each type's facts are kept. A type with an unbounded rule is never pruned.
        unbounded = {rule.type for rule in self.rules if rule.window is None}
        windows: dict[str, timedelta] = {}
        for rule in self.rules:
            if rule.type in unbounded or rule.window is None:
                continue
            windows[rule.type] = max(windows.get(rule.type, rule.window), rule.window)
        return windows


POLICY_V1 = Policy(
    version=1,
    rules=(
        Rule("kyc_expired", "kyc_expired", 1, None, Band.HIGH),
        Rule("chargeback_3_in_30d", "chargeback", 3, timedelta(days=30), Band.HIGH),
        Rule("chargeback_1_in_30d", "chargeback", 1, timedelta(days=30), Band.MEDIUM),
        Rule("velocity_breach_2_in_7d", "velocity_breach", 2, timedelta(days=7), Band.MEDIUM),
        Rule("failed_login_10_in_1h", "failed_login", 10, timedelta(hours=1), Band.MEDIUM),
    ),
)
