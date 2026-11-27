"""The fixes of an audit as a download: one patch file per resource, in a zip.

This is the way to hand fixes over that needs no access to the customer's repository:
no app to install, no token to store, any git host. The person who downloads the archive
applies it with the access they already have.
"""

import io
import re
import zipfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap
from ruamel.yaml.error import YAMLError

from ape.github import helm
from ape.github.patcher import PatchRequest, Target, merge_patch
from ape.github.pull_request import cell, requests_from_findings
from ape.sanitize import contains_placeholder

# Kinds the rules report on. A patch file names its resource completely, so that
# kustomize and kubectl can use it as it is.
_API_VERSION = {
    "Deployment": "apps/v1",
    "StatefulSet": "apps/v1",
    "DaemonSet": "apps/v1",
    "Job": "batch/v1",
    "CronJob": "batch/v1",
    "Pod": "v1",
    "Service": "v1",
    "ServiceAccount": "v1",
    "Namespace": "v1",
    "Role": "rbac.authorization.k8s.io/v1",
    "ClusterRole": "rbac.authorization.k8s.io/v1",
    "RoleBinding": "rbac.authorization.k8s.io/v1",
    "ClusterRoleBinding": "rbac.authorization.k8s.io/v1",
    "NetworkPolicy": "networking.k8s.io/v1",
    "PodDisruptionBudget": "policy/v1",
}

_UNSAFE = re.compile(r"[^a-z0-9._-]+")


@dataclass(frozen=True, slots=True)
class BundleFile:
    path: str
    target: Target
    requests: list[PatchRequest]


@dataclass(slots=True)
class HelmValuesFile:
    path: str
    release: helm.Release
    description: str
    requests: list[PatchRequest]


@dataclass(slots=True)
class FixBundle:
    archive: bytes
    files: list[BundleFile] = field(default_factory=list)
    helm_values: list[HelmValuesFile] = field(default_factory=list)
    manual: list[tuple[PatchRequest, str]] = field(default_factory=list)


def _yaml() -> YAML:
    yaml = YAML(typ="rt")
    # Quotes carry meaning: an unquoted `yes` or `on` is a boolean for YAML 1.1 parsers,
    # which is what kubectl uses.
    yaml.preserve_quotes = True
    yaml.width = 4096
    yaml.indent(mapping=2, sequence=4, offset=2)
    return yaml


def _slug(text: str) -> str:
    """A file name part: Kubernetes names may contain ':' (RBAC) and are not trusted."""
    return _UNSAFE.sub("_", text.lower()).strip("._") or "unnamed"


def _path(target: Target, taken: set[str]) -> str:
    directory = _slug(target.namespace) if target.namespace else "cluster-scoped"
    base = f"patches/{directory}/{_slug(target.kind)}-{_slug(target.name)}"
    path, counter = f"{base}.yaml", 2
    while path in taken:
        path, counter = f"{base}-{counter}.yaml", counter + 1
    taken.add(path)
    return path


def _document(target: Target, patches: list[CommentedMap]) -> CommentedMap:
    """The patches of one resource merged, under the resource's full identity."""
    body = CommentedMap()
    for patch in patches:
        merge_patch(body, patch)
    metadata = body.get("metadata")
    extra = (
        {k: v for k, v in metadata.items() if k not in {"name", "namespace"}}
        if isinstance(metadata, dict)
        else {}
    )
    document = CommentedMap()
    if target.kind in _API_VERSION:
        document["apiVersion"] = _API_VERSION[target.kind]
    document["kind"] = target.kind
    # Always the audited resource: whatever identity the patch text claims is ignored.
    identity = CommentedMap({"name": target.name})
    if target.namespace:
        identity["namespace"] = target.namespace
    identity.update(extra)
    document["metadata"] = identity
    for key, value in body.items():
        if key not in {"apiVersion", "kind", "metadata"}:
            document[key] = value
    return document


def _readme(
    cluster_name: str,
    date: str,
    files: list[BundleFile],
    manual: list[tuple[PatchRequest, str]],
    helm_files: list[HelmValuesFile] | None = None,
) -> str:
    lines = [
        f"# Kubernetes audit fixes for {cell(cluster_name)} ({date})",
        "",
        "Each file under `patches/` is a strategic merge patch for one resource. It contains "
        "only the fields that change. Nothing has been applied to the cluster or to any "
        "repository.",
        "",
        "## How to apply",
        "",
        "Review every patch first. Then use the way that fits how the cluster is deployed:",
        "",
        "1. **Manifests in git (recommended).** Copy the changed fields into the manifest of "
        "the resource, or into the values of the Helm chart that renders it, and open a pull "
        "request as usual.",
        "2. **Kustomize.** Copy `patches/` next to your `kustomization.yaml` and add the entries "
        "from `kustomization.example.yaml`.",
        "3. **Preview against the cluster.** This changes nothing and shows what would change:",
        "",
        "   ```",
        "   kubectl patch <kind> <name> -n <namespace> --type strategic "
        "--patch-file <file> --dry-run=server -o yaml",
        "   ```",
        "",
        "   A change made directly on the cluster is reverted by GitOps tools such as Argo CD "
        "or Flux, so make it in git.",
        "",
    ]
    if files:
        lines += ["## Patches", "", "| File | Resource | Findings |", "| --- | --- | --- |"]
        for f in files:
            found = "; ".join(f"{cell(r.rule_id)} {cell(r.title)}" for r in f.requests)
            lines.append(f"| {cell(f.path)} | {cell(str(f.target))} | {found} |")
        lines.append("")
    if helm_files:
        lines += [
            "## Helm releases",
            "",
            "These resources are rendered by a Helm chart, so a patch of the resource cannot "
            "be committed for them. Each file below holds the same fixes as chart values. "
            "Only fixes whose value key was verified against the chart are included.",
            "",
            "| File | Release | Chart and keys | Findings |",
            "| --- | --- | --- | --- |",
        ]
        for h in helm_files:
            found = "; ".join(f"{cell(r.rule_id)} {cell(r.title)}" for r in h.requests)
            lines.append(
                f"| {cell(h.path)} | {cell(h.release.instance)} | {cell(h.description)} | {found} |"
            )
        lines.append("")
    if manual:
        lines += [
            "## Not included",
            "",
            "These fixes have to be written by hand:",
            "",
        ]
        for request, reason in manual:
            lines.append(
                f"- {cell(request.rule_id)} {cell(request.title)} on "
                f"{cell(str(request.target))}: {cell(reason)}"
            )
        lines.append("")
    return "\n".join(lines)


def _helm_values(
    grouped: dict[Target, list[tuple[PatchRequest, CommentedMap]]],
    objects: dict[tuple[str, str | None, str], dict[str, Any]],
    taken: set[str],
) -> tuple[list[HelmValuesFile], dict[str, str]]:
    """One values overlay per Helm release, for the patches that have verified value keys.

    A strategic merge patch cannot be applied to a chart; these files can be passed to
    ``helm upgrade -f`` or copied into the release's own values file.
    """
    overlays: dict[tuple[str | None, str], dict[str, Any]] = {}
    described: dict[tuple[str | None, str], tuple[helm.Release, list[str], list[PatchRequest]]] = {}
    for target, entries in grouped.items():
        obj = objects.get((target.kind, target.namespace, target.name))
        for request, _ in entries:
            translated = helm.translation_for(obj, request.patch)
            if not isinstance(translated, helm.Translation):
                continue
            known, parent = helm.standalone_parent(translated.release)
            if not known:
                continue
            key = (translated.release.namespace, translated.release.instance)
            _deep_update(overlays.setdefault(key, {}), translated.overlay(parent))
            release, notes, requests = described.setdefault(key, (translated.release, [], []))
            notes.append(translated.describe(parent))
            requests.append(request)

    files: list[HelmValuesFile] = []
    contents: dict[str, str] = {}
    for key in sorted(overlays, key=lambda k: (k[0] or "", k[1])):
        release, notes, requests = described[key]
        base = f"helm-values/{_slug(key[0] or 'cluster')}-{_slug(key[1])}"
        path, counter = f"{base}.yaml", 2
        while path in taken:
            path, counter = f"{base}-{counter}.yaml", counter + 1
        taken.add(path)
        header = (
            f"# Values for Helm release {_comment(release.instance)}"
            f" in namespace {_comment(release.namespace or '-')}.\n"
            "# Pass this file with -f after the release's own values, or copy the keys into them.\n"
        )
        contents[path] = header + _safe_yaml(overlays[key])
        files.append(HelmValuesFile(path, release, "; ".join(dict.fromkeys(notes)), requests))
    return files, contents


def _comment(text: str) -> str:
    """Text inside a YAML comment: one line, nothing that could end the comment."""
    return " ".join(str(text).split())[:120]


def _safe_yaml(data: dict[str, Any]) -> str:
    import yaml as pyyaml

    dumped: str = pyyaml.safe_dump(data, sort_keys=False)
    return dumped


def _deep_update(target: dict[str, Any], source: dict[str, Any]) -> None:
    for key, value in source.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _deep_update(target[key], value)
        else:
            target[key] = value


def build_fix_bundle(
    rows: list[dict[str, Any]],
    *,
    cluster_name: str,
    now: datetime | None = None,
    objects: dict[tuple[str, str | None, str], dict[str, Any]] | None = None,
) -> FixBundle:
    """``rows`` are the findings of one audit. Findings without a patch are skipped."""
    now = now or datetime.now(UTC)
    yaml = _yaml()
    grouped: dict[Target, list[tuple[PatchRequest, CommentedMap]]] = {}
    manual: list[tuple[PatchRequest, str]] = []
    for request in requests_from_findings(rows):
        # Checked again here, not only when the finding was written: a "<secret:...>"
        # placeholder applied to a manifest would overwrite the customer's real value.
        if contains_placeholder(request.patch):
            manual.append((request, "the patch contains a masked value"))
            continue
        try:
            parsed = yaml.load(request.patch)
        except YAMLError:
            parsed = None
        if not isinstance(parsed, CommentedMap):
            manual.append((request, "the patch is not a YAML mapping"))
            continue
        grouped.setdefault(request.target, []).append((request, parsed))

    files: list[BundleFile] = []
    contents: dict[str, str] = {}
    taken: set[str] = set()
    for target in sorted(grouped, key=str):
        entries = grouped[target]
        path = _path(target, taken)
        buffer = io.StringIO()
        yaml.dump(_document(target, [patch for _, patch in entries]), buffer)
        contents[path] = buffer.getvalue()
        files.append(BundleFile(path, target, [request for request, _ in entries]))

    helm_files, helm_contents = _helm_values(grouped, objects or {}, taken)
    date = now.strftime("%Y-%m-%d")
    members = {
        "README.md": _readme(cluster_name, date, files, manual, helm_files),
        **contents,
        **helm_contents,
    }
    if files:
        example = [
            "# Add these entries to the `patches:` list of your kustomization.yaml.",
            "patches:",
        ]
        example += [f"  - path: {f.path}" for f in files]
        members["kustomization.example.yaml"] = "\n".join(example) + "\n"

    archive = io.BytesIO()
    stamp = (now.year, now.month, now.day, now.hour, now.minute, now.second)
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        for name, text in members.items():
            info = zipfile.ZipInfo(name, date_time=stamp)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            bundle.writestr(info, text)
    return FixBundle(archive.getvalue(), files, helm_files, manual)
