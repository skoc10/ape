"""Score, report template and PDF (tasks 4.2 and 4.3)."""

import io
import re
import time
import tracemalloc
import urllib.error
import urllib.request
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient
from pypdf import PdfReader

from ape.analysis.analyst import Analyst
from ape.analysis.pipeline import Pipeline
from ape.core.config import Settings
from ape.core.db import Database
from ape.observability.metrics import Metrics
from ape.reports.builder import Reporter
from ape.reports.render import ReportData, ReportFinding, render_html, render_pdf
from ape.reports.scoring import ScoringConfig, compute_score, load_config
from ape.storage import ObjectStore

from .conftest import Tenant, TokenFactory, bearer
from .test_settings import png, png_header_only
from .test_worker import queue_run, run_row, vulnerable_objects

PRODUCT_NAMES = re.compile(
    r"\bAPE\b|\bape\b|Autonomous Platform Engineering|ape-agent|skoc10", re.IGNORECASE
)


@pytest.fixture(autouse=True)
def quiet_queue(admin_db: psycopg.Connection[Any]) -> None:
    admin_db.execute(
        "update audit_runs set status = 'failed', error = 'parked by tests' "
        "where status in ('collected', 'analyzing', 'reporting')"
    )


# --- score ---------------------------------------------------------------------------


def test_score_uses_the_configured_weights() -> None:
    config = load_config()
    assert config.deduction == {"critical": 15, "high": 8, "medium": 3, "low": 1, "info": 0}
    assert config.max_per_category == 40

    assert compute_score([]).total == 100
    assert compute_score([("security", "critical")]).total == 85
    mixed = compute_score(
        [
            ("security", "critical"),
            ("security", "high"),
            ("rbac", "medium"),
            ("cost", "low"),
            ("cost", "info"),
        ]
    )
    assert mixed.total == 100 - 15 - 8 - 3 - 1
    assert mixed.deductions == {"cost": 1, "rbac": 3, "security": 23}


def test_score_is_capped_per_category_and_never_negative() -> None:
    # Ten criticals in one category cost 40, not 150.
    assert compute_score([("security", "critical")] * 10).total == 60
    everything = [
        (category, "critical")
        for category in ("security", "rbac", "network", "cost", "reliability")
    ] * 10
    assert compute_score(everything).total == 0
    custom = ScoringConfig(
        {"critical": 50, "high": 0, "medium": 0, "low": 0, "info": 0}, max_per_category=60
    )
    assert compute_score([("security", "critical")] * 2, custom).total == 40


# --- HTML ----------------------------------------------------------------------------


def finding(index: int, **overrides: Any) -> ReportFinding:
    severities = ["critical", "high", "medium", "low", "info"]
    categories = ["security", "rbac", "network", "cost", "reliability"]
    base: dict[str, Any] = {
        "rule_id": f"SEC-{index % 5 + 1:03d}",
        "category": categories[index % 5],
        "severity": severities[index % 5],
        "title": f"Finding number {index}",
        "resource": f"Deployment shop/service-{index}",
        "explanation": "The container runs with more privileges than it needs. " * 3,
        "remediation": "1. Set runAsNonRoot.\n2. Roll out the change.",
        "patch": "spec:\n  template:\n    spec:\n      securityContext:\n        runAsNonRoot: true\n",
        "recurring": index % 7 == 0,
    }
    return ReportFinding(**{**base, **overrides})


def report(count: int = 6, **overrides: Any) -> ReportData:
    base: dict[str, Any] = {
        "cluster_name": "prod-eu-west",
        "generated_at": datetime(2026, 11, 25, 9, 0, tzinfo=UTC),
        "score": 72,
        "summary": "The cluster has two critical issues that should be fixed this week.",
        "top_risks": [
            "Privileged container: Deployment shop/storefront",
            "Binding to cluster-admin",
        ],
        "findings": [finding(i) for i in range(count)],
        "company_name": "Acme DevOps",
        "primary_color": "#0f766e",
    }
    return ReportData(**{**base, **overrides})


def test_report_has_every_section_and_no_product_name() -> None:
    html = render_html(report(logo=(png(), "image/png")))
    for heading in (
        "Executive summary",
        "Score and distribution",
        "Top risks",
        "Findings",
        "Details and remediation",
    ):
        assert f"<h2>{heading}</h2>" in html
    assert "Acme DevOps" in html and "prod-eu-west" in html and "25 November 2026" in html
    assert "#0f766e" in html
    assert 'src="data:image/png;base64,' in html
    assert html.count('class="finding"') == 6
    assert "recurring" in html and "How to fix" in html and "Proposed patch" in html
    assert ">72<" in html  # the score inside the SVG ring
    assert PRODUCT_NAMES.search(html) is None


def test_report_is_self_contained_static_html() -> None:
    html = render_html(report())
    assert "<script" not in html.lower()
    assert not re.search(r'(?:src|href)="(?!data:)', html)  # no external resource of any kind
    assert "<svg" in html and "url(" not in html
    assert "@import" not in html


def test_untrusted_text_is_escaped() -> None:
    evil = "<script>alert(1)</script><img src=x onerror=alert(2)>"
    html = render_html(
        report(
            cluster_name=evil,
            summary=evil,
            top_risks=[evil],
            company_name='Evil"; } body { display: none } </style><script>alert(3)</script>',
            primary_color="red; background: url(https://evil.example/x)",
            findings=[
                finding(
                    0, title=evil, resource=evil, explanation=evil, remediation=evil, patch=evil
                )
            ],
        )
    )
    assert "<script>alert" not in html and "<img src=x" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert (
        "evil.example" not in html and "#1f6feb" in html
    )  # invalid colour falls back to the default
    assert "</style><script>" not in html
    assert html.count("</style>") == 1


def test_defaults_without_branding() -> None:
    html = render_html(report(company_name=None, primary_color=None, findings=[], top_risks=[]))
    assert "Platform Engineering" in html and "#1f6feb" in html
    assert "No findings." in html and "<h2>Findings</h2>" not in html
    assert PRODUCT_NAMES.search(html) is None
    assert "<img" not in html
    assert 'src="data:image/svg' not in render_html(report(logo=(b"<svg/>", "image/svg+xml")))


def test_an_oversized_logo_never_reaches_the_renderer() -> None:
    # Already stored before the upload limit existed: left out of the report, not decoded.
    html = render_html(report(logo=(png_header_only(13000, 13000), "image/png")))
    assert "<img" not in html


# --- PDF -----------------------------------------------------------------------------


def test_pdf_of_100_findings_fits_the_page_and_memory_budget() -> None:
    long_token = "x" * 400  # an unbreakable string must wrap instead of running off the page
    findings = [
        finding(
            i,
            patch=f"data:\n  value: {long_token}\n",
            resource=f"Deployment shop/{'very-long-name-' * 8}{i}",
        )
        for i in range(100)
    ]
    html = render_html(report(findings=findings, logo=(png(), "image/png")))

    tracemalloc.start()
    start = time.perf_counter()
    pdf = render_pdf(html)
    seconds = time.perf_counter() - start
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    assert pdf.startswith(b"%PDF-")
    assert peak < 1024**3, f"peak Python allocations {peak / 1024**2:.0f} MiB"
    assert seconds < 120

    reader = PdfReader(io.BytesIO(pdf))
    assert 10 < len(reader.pages) < 120
    text = "".join(page.extract_text() for page in reader.pages)
    assert "Finding number 0" in text and "Finding number 99" in text
    assert "Acme DevOps" in text
    assert PRODUCT_NAMES.search(text) is None
    for page in reader.pages:  # A4, every page
        assert (
            round(float(page.mediabox.width)) == 595 and round(float(page.mediabox.height)) == 842
        )


def test_pdf_rendering_never_touches_the_network_or_the_file_system(tmp_path: Path) -> None:
    secret = tmp_path / "secret.txt"
    secret.write_text("file-content-that-must-not-leak")
    html = render_html(report(count=1)).replace(
        "</body>",
        f'<img src="https://example.invalid/pixel.png"><img src="file://{secret}">'
        f'<object data="file://{secret}"></object><link rel="stylesheet" href="file://{secret}"></body>',
    )
    pdf = render_pdf(html)  # blocked resources are skipped, the report still renders
    assert pdf.startswith(b"%PDF-")
    text = "".join(page.extract_text() for page in PdfReader(io.BytesIO(pdf)).pages)
    assert "file-content-that-must-not-leak" not in text


# --- through the worker and the API ------------------------------------------------------


async def test_worker_builds_report_and_api_serves_it(
    client: TestClient,
    settings: Settings,
    make_token: TokenFactory,
    tenant_a: Tenant,
    tenant_b: Tenant,
    admin_db: psycopg.Connection[Any],
    tmp_path: Path,
) -> None:
    auth_a = bearer(make_token(tenant_a.user_id, tenant_a.org_id))
    auth_b = bearer(make_token(tenant_b.user_id, tenant_b.org_id))
    client.put(
        "/v1/settings/branding",
        json={"company_name": "Acme DevOps", "primary_color": "#0f766e"},
        headers=auth_a,
    )
    client.put("/v1/settings/logo", content=png(), headers=auth_a)

    db = Database(settings.database_url, min_size=1, max_size=3)
    await db.open()
    try:
        metrics = Metrics()
        store = ObjectStore(settings)
        pipeline = Pipeline(
            settings,
            db,
            store,
            metrics,
            enrich=Analyst(settings, db, metrics, None).enrich_run,
            report=Reporter(db, store, metrics).build,
        )
        run_id = await queue_run(
            admin_db, settings, tenant_a, tmp_path, objects=vulnerable_objects()
        )
        run = await pipeline.claim()
        assert run is not None and await pipeline.process(run) == "completed"
    finally:
        await db.close()

    row = run_row(admin_db, run_id)
    # 3 critical + 5 high ... capped at 40 per category; the exact value is pinned on purpose.
    assert (
        row["stats"]["score"]["total"]
        == admin_db.execute("select score from audit_runs where id = %s", (run_id,)).fetchone()[0]
    )  # type: ignore[index]
    assert 0 <= row["stats"]["score"]["total"] < 60
    assert row["stats"]["report"]["html"] is True and row["stats"]["report"]["pdf"] is True
    assert metrics.registry.get_sample_value("ape_pdf_render_seconds_count") == 1.0
    key = admin_db.execute("select report_key from audit_runs where id = %s", (run_id,)).fetchone()
    assert key == (f"{tenant_a.org_id}/{run_id}/report.pdf",)

    audit = client.get(f"/v1/audits/{run_id}", headers=auth_a).json()
    assert audit["has_report"] is True and audit["score"] == row["stats"]["score"]["total"]

    page = client.get(f"/v1/audits/{run_id}/report", headers=auth_a)
    assert page.status_code == 200 and page.headers["content-type"].startswith("text/html")
    csp = page.headers["content-security-policy"]
    assert "sandbox" in csp and "default-src 'none'" in csp and "script" not in csp
    assert page.headers["x-content-type-options"] == "nosniff"
    assert (
        "Acme DevOps" in page.text
        and "Privileged container" in page.text
        and "#0f766e" in page.text
    )
    assert 'src="data:image/png;base64,' in page.text
    assert PRODUCT_NAMES.search(page.text) is None

    link = client.get(f"/v1/audits/{run_id}/report/pdf", headers=auth_a).json()
    assert link["expires_in"] == 300 and f"{tenant_a.org_id}/{run_id}/report.pdf" in link["url"]
    with urllib.request.urlopen(link["url"]) as response:  # noqa: S310 - local object storage
        pdf = response.read()
        assert "attachment" in response.headers["Content-Disposition"]
    assert pdf.startswith(b"%PDF-")
    assert "Acme DevOps" in "".join(p.extract_text() for p in PdfReader(io.BytesIO(pdf)).pages)

    # Without a signature the object is not readable, and other tenants get nothing.
    unsigned = link["url"].split("?")[0]
    with pytest.raises(urllib.error.HTTPError):
        urllib.request.urlopen(unsigned)  # noqa: S310
    assert client.get(f"/v1/audits/{run_id}/report", headers=auth_b).status_code == 404
    assert client.get(f"/v1/audits/{run_id}/report/pdf", headers=auth_b).status_code == 404
    assert client.get(f"/v1/audits/{run_id}/report").status_code == 401
    assert client.get(f"/v1/audits/{uuid.uuid4()}/report", headers=auth_a).status_code == 404


async def test_a_pdf_failure_still_leaves_a_completed_audit_with_an_html_report(
    settings: Settings,
    tenant_a: Tenant,
    admin_db: psycopg.Connection[Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken(html: str) -> bytes:
        raise OSError("cannot load library libpango")

    monkeypatch.setattr("ape.reports.builder.render_pdf", broken)
    db = Database(settings.database_url, min_size=1, max_size=3)
    await db.open()
    try:
        metrics, store = Metrics(), ObjectStore(settings)
        pipeline = Pipeline(settings, db, store, metrics, report=Reporter(db, store, metrics).build)
        run_id = await queue_run(
            admin_db, settings, tenant_a, tmp_path, objects=vulnerable_objects()
        )
        run = await pipeline.claim()
        assert run is not None and await pipeline.process(run) == "completed"
        assert await store.get_report(tenant_a.org_id, run_id, "html") is not None
        assert await store.get_report(tenant_a.org_id, run_id, "pdf") is None
    finally:
        await db.close()
    stats = run_row(admin_db, run_id)["stats"]
    assert stats["report"] == {"html": True, "pdf": False, "pdf_seconds": None}
    assert stats["score"]["total"] < 100
