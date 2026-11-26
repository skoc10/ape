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
class FixBundle:
    archive: bytes
    files: list[BundleFile] = field(default_factory=list)
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
    cluster_name: str, date: str, files: list[BundleFile], manual: list[tuple[PatchRequest, str]]
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


def build_fix_bundle(
    rows: list[dict[str, Any]], *, cluster_name: str, now: datetime | None = None
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

    date = now.strftime("%Y-%m-%d")
    members = {"README.md": _readme(cluster_name, date, files, manual), **contents}
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
    return FixBundle(archive.getvalue(), files, manual)
