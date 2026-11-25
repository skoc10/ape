"""The 15 MVP rules. Each one is a small pure function over the snapshot."""

from collections.abc import Callable, Iterator
from typing import Any

from ape.rules.model import RULES, Finding, ResourceRef, Severity
from ape.rules.snapshot import (
    Obj,
    Snapshot,
    as_dict,
    as_list,
    cpu_millicores,
    matches,
    meta,
    ref,
)

Rule = Callable[[Snapshot], Iterator[Finding]]
_RULES: list[Rule] = []


def rule(fn: Rule) -> Rule:
    _RULES.append(fn)
    return fn


def _finding(
    rule_id: str,
    resource: ResourceRef,
    evidence: dict[str, Any],
    *,
    severity: Severity | None = None,
) -> Finding:
    info = RULES[rule_id]
    return Finding(
        rule_id, info.category, severity or info.severity, info.title, resource, evidence
    )


def _security_context(container: Obj) -> Obj:
    return as_dict(container.get("securityContext"))


# --- security ----------------------------------------------------------------------


@rule
def sec_001_privileged(snapshot: Snapshot) -> Iterator[Finding]:
    for w in snapshot.workloads:
        names = [
            c.get("name")
            for c in w.all_containers
            if _security_context(c).get("privileged") is True
        ]
        if names:
            yield _finding("SEC-001", w.ref, {"containers": names})


@rule
def sec_002_host_access(snapshot: Snapshot) -> Iterator[Finding]:
    for w in snapshot.workloads:
        flags = [f for f in ("hostNetwork", "hostPID", "hostIPC") if w.pod_spec.get(f) is True]
        host_paths = [
            as_dict(v.get("hostPath")).get("path")
            for v in as_list(w.pod_spec.get("volumes"))
            if isinstance(v, dict) and isinstance(v.get("hostPath"), dict)
        ]
        if flags or host_paths:
            yield _finding("SEC-002", w.ref, {"flags": flags, "hostPaths": host_paths})


@rule
def sec_003_runs_as_root(snapshot: Snapshot) -> Iterator[Finding]:
    for w in snapshot.workloads:
        pod_context = as_dict(w.pod_spec.get("securityContext"))
        offenders: list[str] = []
        explicit_root = False
        for c in w.all_containers:
            context = {**pod_context, **_security_context(c)}
            user = context.get("runAsUser")
            if user == 0:
                explicit_root = True
                offenders.append(str(c.get("name")))
            elif context.get("runAsNonRoot") is not True and user is None:
                offenders.append(str(c.get("name")))
        if offenders:
            yield _finding(
                "SEC-003",
                w.ref,
                {"containers": offenders, "explicitRoot": explicit_root},
                severity="high" if explicit_root else None,
            )


@rule
def sec_004_privilege_escalation(snapshot: Snapshot) -> Iterator[Finding]:
    for w in snapshot.workloads:
        names = [
            c.get("name")
            for c in w.all_containers
            if _security_context(c).get("allowPrivilegeEscalation") is not False
        ]
        if names:
            yield _finding("SEC-004", w.ref, {"containers": names})


def _mutable_image(image: Any) -> bool:
    if not isinstance(image, str) or not image or "@sha256:" in image:
        return False
    last_segment = image.rsplit("/", 1)[-1]
    if ":" not in last_segment:
        return True  # no tag at all means :latest
    return last_segment.rsplit(":", 1)[1] == "latest"


@rule
def sec_005_image_tag(snapshot: Snapshot) -> Iterator[Finding]:
    for w in snapshot.workloads:
        images = sorted(
            {str(c.get("image")) for c in w.all_containers if _mutable_image(c.get("image"))}
        )
        if images:
            yield _finding("SEC-005", w.ref, {"images": images})


# --- rbac --------------------------------------------------------------------------

# Roles shipped with Kubernetes are broad by design and not the customer's finding.
_BUILTIN_ROLES = frozenset({"cluster-admin", "admin", "edit", "view"})
_BUILTIN_PREFIXES = ("system:", "kubeadm:")


def _builtin(name: str) -> bool:
    return name in _BUILTIN_ROLES or name.startswith(_BUILTIN_PREFIXES)


def _roles(snapshot: Snapshot) -> Iterator[Obj]:
    for kind in ("ClusterRole", "Role"):
        for role in snapshot.kind(kind):
            if not _builtin(str(meta(role).get("name", ""))):
                yield role


@rule
def rbac_001_wildcards(snapshot: Snapshot) -> Iterator[Finding]:
    for role in _roles(snapshot):
        wildcard_verbs = wildcard_resources = False
        for r in as_list(role.get("rules")):
            entry = as_dict(r)
            wildcard_verbs = wildcard_verbs or "*" in as_list(entry.get("verbs"))
            wildcard_resources = wildcard_resources or "*" in as_list(entry.get("resources"))
        if wildcard_verbs or wildcard_resources:
            yield _finding(
                "RBAC-001",
                ref(role),
                {"wildcardVerbs": wildcard_verbs, "wildcardResources": wildcard_resources},
                severity="critical" if wildcard_verbs and wildcard_resources else None,
            )


@rule
def rbac_002_cluster_admin(snapshot: Snapshot) -> Iterator[Finding]:
    for kind in ("ClusterRoleBinding", "RoleBinding"):
        for binding in snapshot.kind(kind):
            role_ref = as_dict(binding.get("roleRef"))
            if role_ref.get("kind") != "ClusterRole" or role_ref.get("name") != "cluster-admin":
                continue
            if _builtin(str(meta(binding).get("name", ""))):
                continue
            subjects = [
                {k: s.get(k) for k in ("kind", "namespace", "name") if s.get(k)}
                for s in as_list(binding.get("subjects"))
                if isinstance(s, dict)
            ]
            subjects = [
                s for s in subjects if not str(s.get("name", "")).startswith(_BUILTIN_PREFIXES)
            ]
            if subjects:
                yield _finding("RBAC-002", ref(binding), {"subjects": subjects})


@rule
def rbac_003_default_service_account(snapshot: Snapshot) -> Iterator[Finding]:
    default_automount = {
        meta(sa).get("namespace"): sa.get("automountServiceAccountToken")
        for sa in snapshot.kind("ServiceAccount")
        if meta(sa).get("name") == "default"
    }
    for w in snapshot.workloads:
        account = (
            w.pod_spec.get("serviceAccountName") or w.pod_spec.get("serviceAccount") or "default"
        )
        if account != "default":
            continue
        automount = w.pod_spec.get("automountServiceAccountToken")
        if automount is None:
            automount = default_automount.get(w.ref.namespace)
        if automount is not False:
            yield _finding("RBAC-003", w.ref, {"serviceAccount": "default", "tokenMounted": True})


@rule
def rbac_004_list_secrets(snapshot: Snapshot) -> Iterator[Finding]:
    for role in _roles(snapshot):
        verbs: set[str] = set()
        for r in as_list(role.get("rules")):
            entry = as_dict(r)
            # Explicit grants only: '*' resources are already reported by RBAC-001.
            if "secrets" in as_list(entry.get("resources")):
                verbs |= {str(v) for v in as_list(entry.get("verbs"))}
        if verbs & {"list", "watch", "*"}:
            yield _finding("RBAC-004", ref(role), {"verbs": sorted(verbs)})


# --- network -----------------------------------------------------------------------


@rule
def net_001_no_network_policy(snapshot: Snapshot) -> Iterator[Finding]:
    protected = {meta(p).get("namespace") for p in snapshot.kind("NetworkPolicy")}
    populated: dict[str, int] = {}
    for w in snapshot.workloads:
        if w.ref.namespace:
            populated[w.ref.namespace] = populated.get(w.ref.namespace, 0) + 1
    for namespace in snapshot.kind("Namespace"):
        name = str(meta(namespace).get("name", ""))
        # An empty namespace has nothing to protect; reporting it would only be noise.
        if name in populated and name not in protected:
            yield _finding(
                "NET-001", ResourceRef("Namespace", None, name), {"workloads": populated[name]}
            )


@rule
def net_002_exposed_service(snapshot: Snapshot) -> Iterator[Finding]:
    for service in snapshot.kind("Service"):
        spec = as_dict(service.get("spec"))
        if spec.get("type") in {"LoadBalancer", "NodePort"}:
            ports = [as_dict(p).get("port") for p in as_list(spec.get("ports"))]
            yield _finding(
                "NET-002",
                ref(service),
                {
                    "type": spec.get("type"),
                    "ports": ports,
                    "sourceRangesRestricted": bool(as_list(spec.get("loadBalancerSourceRanges"))),
                },
            )


# --- cost --------------------------------------------------------------------------


@rule
def cost_001_missing_resources(snapshot: Snapshot) -> Iterator[Finding]:
    for w in snapshot.workloads:
        missing: dict[str, list[str]] = {}
        for c in w.containers:
            resources = as_dict(c.get("resources"))
            requests, limits = as_dict(resources.get("requests")), as_dict(resources.get("limits"))
            gaps = [
                label
                for label, present in (
                    ("requests.cpu", "cpu" in requests),
                    ("requests.memory", "memory" in requests),
                    ("limits.memory", "memory" in limits),
                )
                if not present
            ]
            if gaps:
                missing[str(c.get("name"))] = gaps
        if missing:
            yield _finding("COST-001", w.ref, {"missing": missing})


# Below this request size the saving is not worth a finding.
_MIN_REQUEST_MILLICORES = 100.0
_OVERPROVISION_FACTOR = 3.0


@rule
def cost_002_overprovisioned_cpu(snapshot: Snapshot) -> Iterator[Finding]:
    usage_by_pod: dict[tuple[Any, Any], float] = {}
    for m in snapshot.kind("PodMetrics"):
        total = 0.0
        for c in as_list(m.get("containers")):
            total += cpu_millicores(as_dict(as_dict(c).get("usage")).get("cpu")) or 0.0
        usage_by_pod[(meta(m).get("namespace"), meta(m).get("name"))] = total
    if not usage_by_pod:
        return
    pods = snapshot.kind("Pod")
    for w in snapshot.workloads:
        if not w.long_running:
            continue
        requested = sum(
            cpu_millicores(as_dict(as_dict(c.get("resources")).get("requests")).get("cpu")) or 0.0
            for c in w.containers
        )
        if requested < _MIN_REQUEST_MILLICORES:
            continue
        if w.ref.kind == "Pod":
            samples = [usage_by_pod.get((w.ref.namespace, w.ref.name))]
        else:
            selector = as_dict(w.obj.get("spec")).get("selector")
            samples = [
                usage_by_pod.get((meta(p).get("namespace"), meta(p).get("name")))
                for p in pods
                if meta(p).get("namespace") == w.ref.namespace
                and matches(selector, meta(p).get("labels"))
            ]
        measured = [s for s in samples if s is not None]
        if not measured:
            continue
        used = sum(measured) / len(measured)
        if requested > _OVERPROVISION_FACTOR * max(used, 1.0):
            yield _finding(
                "COST-002",
                w.ref,
                {
                    "requestedMillicores": round(requested),
                    "usedMillicores": round(used),
                    "pods": len(measured),
                },
            )


# --- reliability -------------------------------------------------------------------


@rule
def rel_001_missing_probes(snapshot: Snapshot) -> Iterator[Finding]:
    for w in snapshot.workloads:
        if not w.long_running:
            continue  # batch workloads finish on their own; probes are not expected
        missing: dict[str, list[str]] = {}
        for c in w.containers:
            gaps = [
                p for p in ("livenessProbe", "readinessProbe") if not isinstance(c.get(p), dict)
            ]
            if gaps:
                missing[str(c.get("name"))] = gaps
        if missing:
            yield _finding("REL-001", w.ref, {"missing": missing})


@rule
def rel_002_single_replica(snapshot: Snapshot) -> Iterator[Finding]:
    budgets = snapshot.kind("PodDisruptionBudget")
    for w in snapshot.workloads:
        if w.ref.kind not in {"Deployment", "StatefulSet"}:
            continue
        replicas = as_dict(w.obj.get("spec")).get("replicas", 1)
        if replicas != 1:
            continue
        covered = any(
            meta(b).get("namespace") == w.ref.namespace
            and matches(as_dict(b.get("spec")).get("selector"), w.pod_labels)
            for b in budgets
        )
        if not covered:
            yield _finding("REL-002", w.ref, {"replicas": 1, "podDisruptionBudget": False})


# --- engine ------------------------------------------------------------------------


def run_rules(objects: list[Obj], *, include_system_namespaces: bool = False) -> list[Finding]:
    """Runs every rule and returns the findings in a stable order, one per (rule, resource)."""
    snapshot = Snapshot(objects, include_system_namespaces=include_system_namespaces)
    unique: dict[str, Finding] = {}
    for check in _RULES:
        for finding in check(snapshot):
            unique.setdefault(finding.fingerprint, finding)
    return sorted(unique.values(), key=lambda f: (f.rule_id, str(f.resource)))
