"""Audit score: starts at 100, findings deduct points, capped per category."""

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
    deduction: dict[str, int]
    max_per_category: int


@lru_cache
def load_config() -> ScoringConfig:
    raw = tomllib.loads((Path(__file__).parent / "scoring.toml").read_text())
    return ScoringConfig(dict(raw["deduction"]), int(raw["limits"]["max_per_category"]))


@dataclass(frozen=True, slots=True)
class Score:
    total: int
    deductions: dict[str, int]  # per category, after the cap


def compute_score(
    findings: Iterable[tuple[str, str]], config: ScoringConfig | None = None
) -> Score:
    """``findings`` are (category, severity) pairs."""
    config = config or load_config()
    raw: dict[str, int] = defaultdict(int)
    for category, severity in findings:
        raw[category] += config.deduction.get(severity, 0)
    deductions = {
        category: min(points, config.max_per_category) for category, points in sorted(raw.items())
    }
    return Score(total=max(0, 100 - sum(deductions.values())), deductions=deductions)
