"""Report rendering. One Jinja2 template produces the HTML shown in the panel; the same
HTML goes through WeasyPrint for the PDF.

White label: the report carries the tenant's company name, colour and logo and never
the name of this product.
"""

import base64
import os
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from jinja2 import Environment, PackageLoader, select_autoescape
from markupsafe import Markup

from ape.reports.charts import category_bars, score_ring
from ape.reports.logo import acceptable
from ape.reports.scoring import SEVERITIES

DEFAULT_COLOR = "#1f6feb"
DEFAULT_COMPANY = "Platform Engineering"
_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")
_LOGO_TYPES = {"image/png", "image/jpeg"}


@dataclass(frozen=True, slots=True)
class ReportFinding:
    rule_id: str
    category: str
    severity: str
    title: str
    resource: str
    explanation: str | None
    remediation: str | None
    patch: str | None
    recurring: bool


@dataclass(frozen=True, slots=True)
class ReportData:
    cluster_name: str
    generated_at: datetime
    score: int
    summary: str
    top_risks: list[str]
    findings: list[ReportFinding]
    truncated: bool = False
    company_name: str | None = None
    primary_color: str | None = None
    logo: tuple[bytes, str] | None = None  # (bytes, content type)
    extra: dict[str, Any] = field(default_factory=dict)


def _css_string(value: str) -> Markup:
    """Makes text safe inside a CSS string literal (used for the page footer)."""
    cleaned = re.sub(r"[\\\"\n\r\f<>]", " ", value)
    return Markup(cleaned)  # noqa: S704 - everything that can leave the string was removed


_env = Environment(
    loader=PackageLoader("ape.reports", "templates"),
    autoescape=select_autoescape(default=True, default_for_string=True),
    trim_blocks=True,
    lstrip_blocks=True,
)
_env.filters["css_string"] = _css_string


def render_html(data: ReportData) -> str:
    """A self-contained HTML document: inline CSS, inline SVG, no scripts, no external URLs."""
    color = (
        data.primary_color
        if data.primary_color and _COLOR.match(data.primary_color)
        else DEFAULT_COLOR
    )
    logo_data_uri = None
    # The size is checked again here: a logo stored before the upload limit existed, or
    # written to the bucket by other means, must not reach the PDF renderer either.
    if data.logo is not None and data.logo[1] in _LOGO_TYPES and acceptable(data.logo[0]):
        logo_data_uri = f"data:{data.logo[1]};base64,{base64.b64encode(data.logo[0]).decode()}"
    severity_counts = Counter(f.severity for f in data.findings)
    category_counts = Counter(f.category for f in data.findings)
    return _env.get_template("report.html.j2").render(
        cluster_name=data.cluster_name,
        generated_on=data.generated_at.strftime("%d %B %Y"),
        company_name=(data.company_name or DEFAULT_COMPANY).strip() or DEFAULT_COMPANY,
        color=color,
        logo_data_uri=logo_data_uri,
        summary=data.summary,
        top_risks=data.top_risks[:5],
        findings=data.findings,
        truncated=data.truncated,
        severities=SEVERITIES,
        severity_counts=severity_counts,
        score_ring=score_ring(data.score, color),
        category_bars=category_bars(dict(category_counts), color),
    )


def _find_homebrew_libraries() -> None:
    """Local development on macOS: Pango and friends live under Homebrew's prefix, which
    the dynamic loader does not search for a Python that was not built by Homebrew."""
    if sys.platform != "darwin":
        return
    paths = [p for p in os.environ.get("DYLD_FALLBACK_LIBRARY_PATH", "").split(":") if p]
    for candidate in ("/opt/homebrew/lib", "/usr/local/lib"):
        if candidate not in paths and os.path.isdir(candidate):
            paths.append(candidate)
    os.environ["DYLD_FALLBACK_LIBRARY_PATH"] = ":".join(paths)


def render_pdf(html: str) -> bytes:
    """A4 PDF from the report HTML. CPU and memory heavy: call it from the worker only."""
    _find_homebrew_libraries()
    # Imported lazily: needs system libraries that only the worker image has.
    from weasyprint import HTML
    from weasyprint.urls import URLFetcher

    # Inline data only: rendering a report never triggers a network or file request.
    fetcher = URLFetcher(allowed_protocols=["data"], allow_redirects=False)
    pdf: bytes = HTML(string=html, url_fetcher=fetcher).write_pdf()
    return pdf
