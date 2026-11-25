"""Builds the pull request for one audit: which patches apply, the commit, the description."""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from ape.github.patcher import Applied, Manual, PatchRequest, PatchResult, Target, apply_patches
from ape.sanitize import contains_placeholder

_RULE_LABEL = {
    "exact": "exact match (kind, namespace, name)",
    "kustomize_namespace": "namespace taken from kustomization.yaml",
    "kind_name": "fallback: matched by kind and name",
}


@dataclass(frozen=True, slots=True)
class PullRequestPlan:
    branch: str
    title: str
    commit_message: str
    body: str
    result: PatchResult


def requests_from_findings(rows: list[dict[str, Any]]) -> list[PatchRequest]:
    return [
        PatchRequest(
            fingerprint=r["fingerprint"],
            rule_id=r["rule_id"],
            title=r["title"],
            target=Target(r["resource_kind"], r["resource_ns"], r["resource_name"]),
            patch=r["patch"],
        )
        for r in rows
        if r.get("patch")
    ]


def _cell(text: str) -> str:
    """Text inside a Markdown table cell or line: no table breaks, HTML, links or mentions."""
    cleaned = " ".join(str(text).split())
    for char, replacement in (
        ("\\", "\\\\"),  # first, so that a trailing backslash cannot unescape what follows
        ("|", "\\|"),
        ("[", "\\["),  # no links or images of the text's own making
        ("]", "\\]"),
        ("<", "&lt;"),
        (">", "&gt;"),
        ("@", "@\u200b"),
        ("`", "'"),
    ):
        cleaned = cleaned.replace(char, replacement)
    return cleaned[:200]


def _fence(code: str) -> str:
    """A fenced code block that the content cannot close early."""
    fence = "```"
    while fence in code:
        fence += "`"
    return f"{fence}yaml\n{code.rstrip()}\n{fence}"


def _applied_table(applied: list[Applied]) -> list[str]:
    lines = ["| Finding | Resource | File | Matched by |", "| --- | --- | --- | --- |"]
    for a in applied:
        lines.append(
            f"| {_cell(a.request.rule_id)} {_cell(a.request.title)} | {_cell(str(a.request.target))} "
            f"| {_cell(a.path)} | {_RULE_LABEL[a.rule]} |"
        )
    return lines


def _manual_section(manual: list[Manual]) -> list[str]:
    lines = [
        "## To apply manually",
        "",
        "These fixes could not be placed in a file automatically (for example resources "
        "rendered by Helm). Apply them where the resource is defined.",
        "",
    ]
    for m in manual:
        lines += [
            f"**{_cell(m.request.rule_id)} {_cell(m.request.title)}** on {_cell(str(m.request.target))}",
            f"Reason: {_cell(m.reason)}",
            "",
            _fence(m.request.patch),
            "",
        ]
    return lines


def plan_pull_request(
    sources: dict[str, str],
    rows: list[dict[str, Any]],
    *,
    cluster_name: str,
    summary: str,
    score: int | None,
    report_url: str | None,
    now: datetime | None = None,
) -> PullRequestPlan:
    now = now or datetime.now(UTC)
    # Checked again here, not only when the finding was written: a "<secret:...>" placeholder
    # merged into the repository would overwrite the customer's real value.
    requests = requests_from_findings(rows)
    masked = [r for r in requests if contains_placeholder(r.patch)]
    result = apply_patches(sources, [r for r in requests if not contains_placeholder(r.patch)])
    result.manual += [Manual(r, "the patch contains a masked value") for r in masked]
    date = now.strftime("%Y-%m-%d")
    title = f"Kubernetes audit fixes for {_cell(cluster_name)} ({date})"
    body = [
        f"Fixes proposed by the Kubernetes audit of **{_cell(cluster_name)}** on {date}.",
        "",
        "Nothing was applied to the cluster. Review the changes, merge, and deploy through your usual process.",
        "",
        "## Summary",
        "",
        _cell(summary) if summary else "See the findings below.",
        "",
    ]
    if score is not None:
        body += [f"Audit score: **{score} / 100**", ""]
    if report_url:
        body += [f"Full report: {report_url}", ""]
    if result.applied:
        body += ["## Changes in this pull request", "", *_applied_table(result.applied), ""]
    if result.manual:
        body += _manual_section(result.manual)
    return PullRequestPlan(
        branch=f"audit/{now.strftime('%Y%m%d-%H%M%S')}",
        title=title,
        commit_message=f"Apply Kubernetes audit fixes ({len(result.applied)} findings)",
        body="\n".join(body).strip() + "\n",
        result=result,
    )
