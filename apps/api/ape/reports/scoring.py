"""Audit score: a 0-100 score per category, combined into a weighted total.

See scoring.toml for the model and its numbers.
"""

import tomllib
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

CATEGORIES = ("security", "rbac", "network", "cost", "reliability")
SEVERITIES = ("critical", "high", "medium", "low", "info")


@dataclass(frozen=True, slots=True)
class ScoringConfig:
    penalty: dict[str, float]
    repeat_factor: float
    half: float
    category_weight: dict[str, float]


@lru_cache
def load_config() -> ScoringConfig:
    raw = tomllib.loads((Path(__file__).parent / "scoring.toml").read_text())
    return ScoringConfig(
        penalty={k: float(v) for k, v in raw["penalty"].items()},
        repeat_factor=float(raw["repeat"]["factor"]),
        half=float(raw["curve"]["half"]),
        category_weight={k: float(v) for k, v in raw["category"].items()},
    )


@dataclass(frozen=True, slots=True)
class Score:
    total: int
    categories: dict[str, int]  # score of every category, 0-100


def compute_score(
    findings: Iterable[tuple[str, str, str]], config: ScoringConfig | None = None
) -> Score:
    """``findings`` are (category, severity, rule id) triples."""
    config = config or load_config()
    by_rule: dict[tuple[str, str], list[float]] = defaultdict(list)
    for category, severity, rule_id in findings:
        by_rule[(category, rule_id)].append(config.penalty.get(severity, 0.0))

    penalty: dict[str, float] = defaultdict(float)
    for (category, _), points in by_rule.items():
        # Worst first, so the full weight goes to the most severe finding of the rule.
        for position, value in enumerate(sorted(points, reverse=True)):
            penalty[category] += value * config.repeat_factor**position

    categories = {
        category: round(100 * config.half / (config.half + penalty[category]))
        for category in CATEGORIES
    }
    weights = {c: config.category_weight.get(c, 0.0) for c in CATEGORIES}
    total = sum(categories[c] * weights[c] for c in CATEGORIES) / (sum(weights.values()) or 1.0)
    return Score(total=max(0, min(100, round(total))), categories=categories)
