"""Builds the pull request for one audit: which patches apply, the commit, the description."""

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any

import yaml

from ape.github import helm
from ape.github.patcher import (
    Applied,
    Manual,
    PatchRequest,
    PatchResult,
    Target,
    apply_patches,
    merge_into_document,
)
from ape.sanitize import contains_placeholder

_RULE_LABEL = {
    "exact": "exact match (kind, namespace, name)",
    "kustomize_namespace": "namespace taken from kustomization.yaml",
    "kind_name": "fallback: matched by kind and name",
    "helm_values": "Helm values",
}

ObjectIndex = dict[tuple[str, str | None, str], dict[str, Any]]
_NO_MANIFEST = "no manifest for this resource"


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


def cell(text: str) -> str:
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
            f"| {cell(a.request.rule_id)} {cell(a.request.title)} | {cell(str(a.request.target))} "
            f"| {cell(a.path)} | {_RULE_LABEL[a.rule]}{': ' + cell(a.detail) if a.detail else ''} |"
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
            f"**{cell(m.request.rule_id)} {cell(m.request.title)}** on {cell(str(m.request.target))}",
            f"Reason: {cell(m.reason)}",
            "",
            _fence(m.request.patch),
            "",
        ]
    return lines


def apply_helm_values(sources: dict[str, str], result: PatchResult, objects: ObjectIndex) -> None:
    """Second pass over the patches that found no manifest: resources rendered by a Helm
    chart are fixed in the release's values file, when both the file and the key are known.

    Patches that could be translated but have no file here stay manual, shown as the values
    to set instead of a patch nobody can apply to a chart.
    """
    still_manual: list[Manual] = []
    for manual in result.manual:
        request = manual.request
        if not manual.reason.startswith(_NO_MANIFEST):
            still_manual.append(manual)
            continue
        target = request.target
        translated = helm.translation_for(
            objects.get((target.kind, target.namespace, target.name)), request.patch
        )
        if translated is None:
            still_manual.append(manual)
            continue
        if isinstance(translated, str):
            still_manual.append(Manual(request, translated))
            continue
        release = translated.release
        found = helm.find_values(release, sources)
        if isinstance(found, str):
            known, parent = helm.standalone_parent(release)
            if not known:
                still_manual.append(Manual(request, f"rendered by {release}; {found}"))
                continue
            suggestion = yaml.safe_dump(translated.overlay(parent), sort_keys=False)
            still_manual.append(
                Manual(
                    replace(request, patch=suggestion),
                    f"rendered by {release}; {found}. Set these values for the release "
                    f"({translated.describe(parent)})",
                )
            )
            continue
        overlay = translated.overlay(found.parent, found.prefix)
        current = result.files.get(found.path, sources[found.path])
        document = list(yaml.safe_load_all(current))[found.document]
        problem = helm.conflict(document, overlay)
        updated = (
            None if problem else merge_into_document(current, found.path, found.document, overlay)
        )
        if updated is None:
            reason = problem or "its values file cannot be edited safely"
            still_manual.append(Manual(request, f"rendered by {release}; {reason}"))
            continue
        if updated != current:
            result.files[found.path] = updated
        result.applied.append(
            Applied(
                request,
                found.path,
                "helm_values",
                f"{translated.describe(found.parent)} (file found by {found.found_by})",
            )
        )
    result.manual[:] = still_manual


def plan_pull_request(
    sources: dict[str, str],
    rows: list[dict[str, Any]],
    *,
    cluster_name: str,
    summary: str,
    score: int | None,
    now: datetime | None = None,
    objects: ObjectIndex | None = None,
) -> PullRequestPlan:
    now = now or datetime.now(UTC)
    # Checked again here, not only when the finding was written: a "<secret:...>" placeholder
    # merged into the repository would overwrite the customer's real value.
    requests = requests_from_findings(rows)
    masked = [r for r in requests if contains_placeholder(r.patch)]
    result = apply_patches(sources, [r for r in requests if not contains_placeholder(r.patch)])
    if objects:
        apply_helm_values(sources, result, objects)
    result.manual += [Manual(r, "the patch contains a masked value") for r in masked]
    date = now.strftime("%Y-%m-%d")
    title = f"Kubernetes audit fixes for {cell(cluster_name)} ({date})"
    body = [
        f"Fixes proposed by the Kubernetes audit of **{cell(cluster_name)}** on {date}.",
        "",
        "Nothing was applied to the cluster. Review the changes, merge, and deploy through your usual process.",
        "",
        "## Summary",
        "",
        cell(summary) if summary else "See the findings below.",
        "",
    ]
    if score is not None:
        body += [f"Audit score: **{score} / 100**", ""]
    # No link to the report: it lives behind the panel's login, and the panel's address
    # has no place in the customer's repository (white label).
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
