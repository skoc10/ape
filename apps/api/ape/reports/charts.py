"""Static SVG charts. WeasyPrint runs no JavaScript, so nothing here is interactive."""

import math
from html import escape

from ape.reports.scoring import CATEGORIES

_TRACK = "#e5e7eb"


def score_ring(score: int, color: str) -> str:
    """A donut that is filled to ``score`` percent, with the number in the middle."""
    score = max(0, min(100, score))
    radius, stroke = 52, 12
    circumference = 2 * math.pi * radius
    filled = circumference * score / 100
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="140" height="140" viewBox="0 0 140 140" '
        f'role="img" aria-label="Score {score} out of 100">'
        f'<circle cx="70" cy="70" r="{radius}" fill="none" stroke="{_TRACK}" stroke-width="{stroke}"/>'
        f'<circle cx="70" cy="70" r="{radius}" fill="none" stroke="{escape(color)}" '
        f'stroke-width="{stroke}" stroke-linecap="round" '
        f'stroke-dasharray="{filled:.2f} {circumference - filled:.2f}" transform="rotate(-90 70 70)"/>'
        f'<text x="70" y="78" text-anchor="middle" font-size="34" font-weight="700" '
        f'fill="#111827">{score}</text>'
        '<text x="70" y="98" text-anchor="middle" font-size="11" fill="#6b7280">out of 100</text>'
        "</svg>"
    )


def category_bars(counts: dict[str, int], color: str) -> str:
    """Horizontal bars: number of findings per category."""
    row_height, label_width, bar_width = 26, 90, 260
    largest = max([*counts.values(), 1])
    height = row_height * len(CATEGORIES)
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{label_width + bar_width + 40}" '
        f'height="{height}" role="img" aria-label="Findings per category">'
    ]
    for index, category in enumerate(CATEGORIES):
        count = counts.get(category, 0)
        y = index * row_height
        width = bar_width * count / largest
        parts.append(
            f'<text x="0" y="{y + 17}" font-size="12" fill="#374151">{category}</text>'
            f'<rect x="{label_width}" y="{y + 6}" width="{bar_width}" height="14" rx="3" fill="{_TRACK}"/>'
            f'<rect x="{label_width}" y="{y + 6}" width="{width:.1f}" height="14" rx="3" '
            f'fill="{escape(color)}"/>'
            f'<text x="{label_width + bar_width + 8}" y="{y + 17}" font-size="12" '
            f'fill="#111827">{count}</text>'
        )
    parts.append("</svg>")
    return "".join(parts)
