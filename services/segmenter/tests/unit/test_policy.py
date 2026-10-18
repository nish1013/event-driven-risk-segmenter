from datetime import timedelta

import pytest

from segmenter.domain.policy import POLICY_V1, Band, RuleResult

ALL_ZERO = {rule.name: 0 for rule in POLICY_V1.rules}


def counts(**fired: int) -> dict[str, int]:
    return ALL_ZERO | fired


@pytest.mark.parametrize(
    ("rule", "threshold", "band"),
    [
        ("kyc_expired", 1, Band.HIGH),
        ("chargeback_3_in_30d", 3, Band.HIGH),
        ("chargeback_1_in_30d", 1, Band.MEDIUM),
        ("velocity_breach_2_in_7d", 2, Band.MEDIUM),
        ("failed_login_10_in_1h", 10, Band.MEDIUM),
    ],
)
def test_p3_each_rule_below_at_above(rule: str, threshold: int, band: Band) -> None:
    below = POLICY_V1.rule_band(counts(**{rule: threshold - 1}))
    at = POLICY_V1.rule_band(counts(**{rule: threshold}))
    above = POLICY_V1.rule_band(counts(**{rule: threshold + 1}))

    assert below == RuleResult(Band.LOW, None)
    assert at == RuleResult(band, rule)
    assert above == RuleResult(band, rule)


def test_p3_chargeback_two_of_three_stays_medium() -> None:
    result = POLICY_V1.rule_band(counts(chargeback_3_in_30d=2, chargeback_1_in_30d=2))

    assert result == RuleResult(Band.MEDIUM, "chargeback_1_in_30d")


@pytest.mark.parametrize(
    ("fired", "expected"),
    [
        (
            {"chargeback_1_in_30d": 1, "kyc_expired": 1},
            RuleResult(Band.HIGH, "kyc_expired"),
        ),
        (
            {"chargeback_1_in_30d": 1, "failed_login_10_in_1h": 10},
            RuleResult(Band.MEDIUM, "chargeback_1_in_30d"),
        ),
        (
            {"kyc_expired": 1, "chargeback_3_in_30d": 3, "chargeback_1_in_30d": 3},
            RuleResult(Band.HIGH, "kyc_expired"),
        ),
    ],
)
def test_p3_tie_breaker_table(fired: dict[str, int], expected: RuleResult) -> None:
    assert POLICY_V1.rule_band(counts(**fired)) == expected


def test_p3_no_rule_fires_low() -> None:
    assert POLICY_V1.rule_band(ALL_ZERO) == RuleResult(Band.LOW, None)


def test_p6_prune_windows_skip_kyc_expired() -> None:
    assert POLICY_V1.prune_windows() == {
        "chargeback": timedelta(days=30),
        "velocity_breach": timedelta(days=7),
        "failed_login": timedelta(hours=1),
    }
