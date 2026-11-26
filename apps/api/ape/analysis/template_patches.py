"""Patches that can be written without a model and without guessing a value.

Only fixes where the right value is the same for every workload qualify. Probes need a
path and a port, resource requests need sizes, a non-root user needs an id: those are
left to a person or to the model, which may also decline.
"""

from typing import Any

import yaml

from ape.rules import Finding

# Where the pod spec lives, by kind.
_POD_SPEC_PATH: dict[str, tuple[str, ...]] = {
    "Pod": ("spec",),
    "Deployment": ("spec", "template", "spec"),
    "StatefulSet": ("spec", "template", "spec"),
    "DaemonSet": ("spec", "template", "spec"),
    "Job": ("spec", "template", "spec"),
    "CronJob": ("spec", "jobTemplate", "spec", "template", "spec"),
}


def _dig(obj: Any, path: tuple[str, ...]) -> dict[str, Any]:
    for key in path:
        obj = obj.get(key) if isinstance(obj, dict) else None
    return obj if isinstance(obj, dict) else {}


def _needs_escalation(container: dict[str, Any]) -> bool:
    """Privileged containers and CAP_SYS_ADMIN imply privilege escalation: the API server
    rejects them together with allowPrivilegeEscalation: false."""
    context = container.get("securityContext")
    context = context if isinstance(context, dict) else {}
    capabilities = context.get("capabilities")
    added = capabilities.get("add") if isinstance(capabilities, dict) else None
    added = added if isinstance(added, list) else []
    return context.get("privileged") is True or any(
        str(c).upper() in {"SYS_ADMIN", "CAP_SYS_ADMIN", "ALL"} for c in added
    )


def _sec_004(finding: Finding, obj: dict[str, Any]) -> dict[str, Any] | None:
    path = _POD_SPEC_PATH.get(finding.resource.kind)
    if path is None:
        return None
    pod_spec = _dig(obj, path)
    patch_spec: dict[str, Any] = {}
    for field in ("containers", "initContainers"):
        entries = []
        containers = pod_spec.get(field)
        for container in containers if isinstance(containers, list) else []:
            if not isinstance(container, dict) or not isinstance(container.get("name"), str):
                continue
            context = container.get("securityContext")
            context = context if isinstance(context, dict) else {}
            if context.get("allowPrivilegeEscalation") is False or _needs_escalation(container):
                continue
            entries.append(
                {"name": container["name"], "securityContext": {"allowPrivilegeEscalation": False}}
            )
        if entries:
            patch_spec[field] = entries
    if not patch_spec:
        return None
    patch: dict[str, Any] = patch_spec
    for key in reversed(path):
        patch = {key: patch}
    return patch


_TEMPLATES = {"SEC-004": _sec_004}


def template_patch(finding: Finding, obj: dict[str, Any] | None) -> str | None:
    """A strategic merge patch for the finding's own resource, or None."""
    build = _TEMPLATES.get(finding.rule_id)
    if build is None or not isinstance(obj, dict):
        return None
    patch = build(finding, obj)
    return yaml.safe_dump(patch, sort_keys=False) if patch else None
