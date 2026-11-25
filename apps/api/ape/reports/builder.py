"""Pipeline stage that turns a finished analysis into the stored HTML and PDF report."""

import asyncio
import time
from datetime import UTC, datetime
from typing import Any

import structlog

from ape.analysis.pipeline import ClaimedRun
from ape.core.db import Database
from ape.observability.metrics import Metrics
from ape.reports.render import ReportData, ReportFinding, render_html, render_pdf
from ape.reports.scoring import compute_score
from ape.repos import audit_runs, clusters, findings, org_settings
from ape.storage import ObjectStore

log = structlog.get_logger(__name__)


class Reporter:
    def __init__(
        self, db: Database, store: ObjectStore, metrics: Metrics, *, pdf: bool = True
    ) -> None:
        self._db = db
        self._store = store
        self._metrics = metrics
        self._pdf = pdf

    async def build(self, run: ClaimedRun, stats: dict[str, Any]) -> dict[str, Any]:
        """Scores the run, renders the report and stores it. Returns stats to merge."""
        async with self._db.tenant(run.org_id) as tc:
            rows = await findings.list_for_run(tc, run.id)
            cluster = await clusters.get(tc, run.cluster_id)
            settings = await org_settings.get(tc)
            audit = await audit_runs.get(tc, run.id)
        branding = settings["branding"] or {}
        logo = await self._store.get_logo(run.org_id) if branding.get("logo_key") else None
        score = compute_score((r["category"], r["severity"]) for r in rows)

        data = ReportData(
            cluster_name=str(cluster["name"]) if cluster else "cluster",
            generated_at=datetime.now(UTC),
            score=score.total,
            summary=str(stats.get("summary") or ""),
            top_risks=[str(r) for r in stats.get("top_risks") or []],
            truncated=bool(audit and (audit["stats"] or {}).get("truncated")),
            company_name=branding.get("company_name"),
            primary_color=branding.get("primary_color"),
            logo=logo,
            findings=[
                ReportFinding(
                    rule_id=r["rule_id"],
                    category=r["category"],
                    severity=r["severity"],
                    title=r["title"],
                    resource=" ".join(
                        [
                            r["resource_kind"],
                            f"{r['resource_ns']}/{r['resource_name']}"
                            if r["resource_ns"]
                            else r["resource_name"],
                        ]
                    ),
                    explanation=r["explanation"],
                    remediation=r["remediation"],
                    patch=r["patch"],
                    recurring=bool(r["recurring"]),
                )
                for r in rows
            ],
        )
        html = await asyncio.to_thread(render_html, data)
        await self._store.put_report(
            run.org_id, run.id, "html", html.encode(), "text/html; charset=utf-8"
        )

        report_key: str | None = None
        pdf_seconds: float | None = None
        if self._pdf:
            start = time.perf_counter()
            try:
                pdf = await asyncio.to_thread(render_pdf, html)
            except Exception as exc:
                # The HTML report and the findings are already there; a PDF problem
                # (fonts, system libraries) must not turn the whole audit into a failure.
                log.error("pdf_render_failed", run_id=str(run.id), error_type=type(exc).__name__)
            else:
                pdf_seconds = time.perf_counter() - start
                self._metrics.pdf_render_seconds.observe(pdf_seconds)
                report_key = await self._store.put_report(
                    run.org_id, run.id, "pdf", pdf, "application/pdf"
                )

        async with self._db.tenant(run.org_id) as tc:
            await audit_runs.set_report(tc, run.id, score=score.total, report_key=report_key)
        log.info("report_built", run_id=str(run.id), score=score.total, pdf=report_key is not None)
        return {
            "score": {"total": score.total, "deductions": score.deductions},
            "report": {
                "html": True,
                "pdf": report_key is not None,
                "pdf_seconds": None if pdf_seconds is None else round(pdf_seconds, 3),
            },
        }
