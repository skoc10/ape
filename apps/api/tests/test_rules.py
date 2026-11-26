"""Rule engine (task 3.3): a positive and a negative case per rule, plus the vulnerable set."""

from pathlib import Path
from typing import Any

import pytest
import yaml

from ape.rules import RULES, Finding, ResourceRef, fingerprint, run_rules
from ape.rules.snapshot import cpu_millicores

VULNERABLE = Path(__file__).resolve().parents[3] / "testdata" / "vulnerable"

Obj = dict[str, Any]

SAFE_CONTAINER: Obj = {
    "name": "app",
    "image": "ghcr.io/acme/app:1.2.3",
    "securityContext": {
        "allowPrivilegeEscalation": False,
        "runAsNonRoot": True,
        "runAsUser": 10001,
    },
    "resources": {"requests": {"cpu": "50m", "memory": "64Mi"}, "limits": {"memory": "128Mi"}},
    "livenessProbe": {"httpGet": {"path": "/live", "port": 8080}},
    "readinessProbe": {"httpGet": {"path": "/ready", "port": 8080}},
}


def deployment(
    name: str = "web",
    namespace: str = "shop",
    *,
    replicas: int = 2,
    pod: Obj | None = None,
    **c: Any,
) -> Obj:
    container = {**SAFE_CONTAINER, **c}
    pod_spec: Obj = {"serviceAccountName": "web", "containers": [container], **(pod or {})}
    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"name": name, "namespace": namespace},
        "spec": {
            "replicas": replicas,
            "selector": {"matchLabels": {"app": name}},
            "template": {"metadata": {"labels": {"app": name}}, "spec": pod_spec},
        },
    }


def namespace(name: str) -> Obj:
    return {"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": name}}


def network_policy(ns: str) -> Obj:
    return {
        "kind": "NetworkPolicy",
        "metadata": {"name": "default-deny", "namespace": ns},
        "spec": {"podSelector": {}},
    }


def role(kind: str, name: str, rules: list[Obj], ns: str | None = None) -> Obj:
    meta: Obj = {"name": name}
    if ns:
        meta["namespace"] = ns
    return {"kind": kind, "metadata": meta, "rules": rules}


def ids(findings: list[Finding], rule_id: str) -> list[str]:
    return [str(f.resource) for f in findings if f.rule_id == rule_id]


BASELINE = [namespace("shop"), network_policy("shop")]


def check(rule_id: str, bad: list[Obj], good: list[Obj], expected: str) -> Finding:
    """The rule fires for `bad` on `expected`, and stays silent for `good`."""
    assert ids(run_rules([*BASELINE, *good]), rule_id) == [], f"{rule_id} fired on the good case"
    found = [f for f in run_rules([*BASELINE, *bad]) if f.rule_id == rule_id]
    assert [str(f.resource) for f in found] == [expected]
    info = RULES[rule_id]
    assert found[0].category == info.category and found[0].title == info.title
    return found[0]


# --- security ----------------------------------------------------------------------


def test_sec_001_privileged() -> None:
    bad = deployment(securityContext={**SAFE_CONTAINER["securityContext"], "privileged": True})
    finding = check("SEC-001", [bad], [deployment()], "Deployment/shop/web")
    assert finding.severity == "critical"
    assert finding.evidence == {"containers": ["app"]}


@pytest.mark.parametrize(
    "pod",
    [
        {"hostNetwork": True},
        {"hostPID": True},
        {"hostIPC": True},
        {"volumes": [{"name": "sock", "hostPath": {"path": "/var/run/docker.sock"}}]},
    ],
)
def test_sec_002_host_access(pod: Obj) -> None:
    good = deployment(pod={"hostNetwork": False, "volumes": [{"name": "tmp", "emptyDir": {}}]})
    check("SEC-002", [deployment(pod=pod)], [good], "Deployment/shop/web")


def test_sec_003_root() -> None:
    explicit = check(
        "SEC-003",
        [deployment(securityContext={"allowPrivilegeEscalation": False, "runAsUser": 0})],
        [deployment()],
        "Deployment/shop/web",
    )
    assert explicit.severity == "high"
    unset = [
        f
        for f in run_rules([deployment(securityContext={"allowPrivilegeEscalation": False})])
        if f.rule_id == "SEC-003"
    ]
    assert unset[0].severity == "medium"
    # A pod-level setting covers every container.
    inherited = deployment(
        securityContext={"allowPrivilegeEscalation": False},
        pod={"securityContext": {"runAsNonRoot": True}},
    )
    assert ids(run_rules([inherited]), "SEC-003") == []


def test_sec_004_privilege_escalation() -> None:
    bad = deployment(securityContext={"runAsNonRoot": True, "runAsUser": 10001})
    check("SEC-004", [bad], [deployment()], "Deployment/shop/web")


@pytest.mark.parametrize(
    ("image", "mutable"),
    [
        ("nginx", True),
        ("nginx:latest", True),
        ("registry.example.com:5000/team/app", True),
        ("registry.example.com:5000/team/app:latest", True),
        ("nginx:1.27.3", False),
        ("registry.example.com:5000/team/app:v2", False),
        ("nginx@sha256:" + "ab" * 32, False),
        ("nginx:latest@sha256:" + "ab" * 32, False),
    ],
)
def test_sec_005_image_tag(image: str, mutable: bool) -> None:
    assert (
        ids(run_rules([deployment(image=image)]), "SEC-005") == ["Deployment/shop/web"]
    ) is mutable


# --- rbac --------------------------------------------------------------------------


def test_rbac_001_wildcards() -> None:
    bad = role("ClusterRole", "ops", [{"apiGroups": ["*"], "resources": ["*"], "verbs": ["*"]}])
    good = role(
        "ClusterRole",
        "reader",
        [{"apiGroups": [""], "resources": ["pods"], "verbs": ["get", "list"]}],
    )
    assert check("RBAC-001", [bad], [good], "ClusterRole/-/ops").severity == "critical"
    verbs_only = role("Role", "half", [{"resources": ["pods"], "verbs": ["*"]}], "shop")
    assert [f.severity for f in run_rules([verbs_only]) if f.rule_id == "RBAC-001"] == ["high"]
    builtin = [
        role("ClusterRole", "cluster-admin", [{"resources": ["*"], "verbs": ["*"]}]),
        role("ClusterRole", "system:controller:x", [{"resources": ["*"], "verbs": ["*"]}]),
    ]
    assert ids(run_rules(builtin), "RBAC-001") == []


def binding(name: str, role_name: str, subjects: list[Obj]) -> Obj:
    return {
        "kind": "ClusterRoleBinding",
        "metadata": {"name": name},
        "roleRef": {
            "apiGroup": "rbac.authorization.k8s.io",
            "kind": "ClusterRole",
            "name": role_name,
        },
        "subjects": subjects,
    }


def test_rbac_002_cluster_admin() -> None:
    bad = binding(
        "ci-admin", "cluster-admin", [{"kind": "ServiceAccount", "name": "ci", "namespace": "ci"}]
    )
    good = binding("ci-view", "view", [{"kind": "ServiceAccount", "name": "ci", "namespace": "ci"}])
    finding = check("RBAC-002", [bad], [good], "ClusterRoleBinding/-/ci-admin")
    assert finding.evidence["subjects"] == [
        {"kind": "ServiceAccount", "namespace": "ci", "name": "ci"}
    ]
    builtin = [
        binding("cluster-admin", "cluster-admin", [{"kind": "Group", "name": "system:masters"}]),
        binding(
            "kubeadm:cluster-admins",
            "cluster-admin",
            [{"kind": "Group", "name": "kubeadm:cluster-admins"}],
        ),
    ]
    assert ids(run_rules(builtin), "RBAC-002") == []


def test_rbac_003_default_service_account() -> None:
    bad = deployment(pod={"serviceAccountName": "default"})
    check("RBAC-003", [bad], [deployment()], "Deployment/shop/web")
    unset = deployment()
    del unset["spec"]["template"]["spec"]["serviceAccountName"]
    assert ids(run_rules([unset]), "RBAC-003") == ["Deployment/shop/web"]
    no_token = deployment(
        pod={"serviceAccountName": "default", "automountServiceAccountToken": False}
    )
    assert ids(run_rules([no_token]), "RBAC-003") == []
    default_sa = {
        "kind": "ServiceAccount",
        "metadata": {"name": "default", "namespace": "shop"},
        "automountServiceAccountToken": False,
    }
    assert ids(run_rules([bad, default_sa]), "RBAC-003") == []


def test_rbac_004_list_secrets() -> None:
    bad = role(
        "Role",
        "reader",
        [{"apiGroups": [""], "resources": ["secrets"], "verbs": ["get", "list"]}],
        "shop",
    )
    good = role(
        "Role",
        "one",
        [{"resources": ["secrets"], "verbs": ["get"], "resourceNames": ["tls"]}],
        "shop",
    )
    check("RBAC-004", [bad], [good], "Role/shop/reader")
    # '*' resources are RBAC-001's finding, not reported twice.
    wildcard = role("ClusterRole", "ops", [{"resources": ["*"], "verbs": ["*"]}])
    assert ids(run_rules([wildcard]), "RBAC-004") == []


def test_platform_components_are_reported_low_and_labelled() -> None:
    everything = [{"apiGroups": ["*"], "resources": ["*"], "verbs": ["*"]}]
    secrets = [{"apiGroups": [""], "resources": ["secrets"], "verbs": ["list"]}]
    subject = [{"kind": "Group", "name": "ops"}]
    exporter = deployment(pod={"hostNetwork": True})
    exporter["metadata"]["name"] = "kube-prometheus-stack-prometheus-node-exporter"
    found = run_rules(
        [
            role("ClusterRole", "argocd-application-controller", everything),
            role("ClusterRole", "cert-manager-controller-issuers", secrets),
            role("Role", "ingress-nginx-internal", secrets, "edge"),
            role("ClusterRole", "secretproviderrotation-role", secrets),
            binding("aks-cluster-admin-binding", "cluster-admin", subject),
            exporter,
            # Not platform components: a similar name is not enough, and neither is
            # living in a namespace that is named like one.
            role("ClusterRole", "argocdx", everything),
            role("Role", "reader", secrets, "argocd"),
            binding("team-admin", "cluster-admin", subject),
        ]
    )
    by_design = {
        str(f.resource): f.evidence.get("platformComponent")
        for f in found
        if f.rule_id in {"RBAC-001", "RBAC-002", "RBAC-004", "SEC-002"}
    }
    assert by_design == {
        "ClusterRole/-/argocd-application-controller": "Argo CD",
        "ClusterRole/-/cert-manager-controller-issuers": "cert-manager",
        "Role/edge/ingress-nginx-internal": "ingress-nginx",
        "ClusterRole/-/secretproviderrotation-role": "Secrets Store CSI driver",
        "ClusterRoleBinding/-/aks-cluster-admin-binding": "Azure Kubernetes Service",
        "Deployment/shop/kube-prometheus-stack-prometheus-node-exporter": "Prometheus stack",
        "ClusterRole/-/argocdx": None,
        "Role/argocd/reader": None,
        "ClusterRoleBinding/-/team-admin": None,
    }
    for f in found:
        if f.evidence.get("platformComponent"):
            assert f.severity == "low"
    severity = {str(f.resource): f.severity for f in found if f.rule_id != "SEC-002"}
    assert severity["ClusterRole/-/argocdx"] == "critical"
    assert severity["ClusterRoleBinding/-/team-admin"] == "critical"
    # Only access the component needs by design is rated down: its other gaps are the
    # customer's to configure and keep their severity.
    others = [f for f in found if f.resource.name.startswith("kube-prometheus-stack")]
    assert all("platformComponent" not in f.evidence for f in others if f.rule_id != "SEC-002")


# --- network -----------------------------------------------------------------------


def test_net_001_namespace_without_network_policy() -> None:
    objects = [namespace("open"), deployment(namespace="open"), namespace("empty")]
    findings = run_rules([*BASELINE, deployment(), *objects])
    assert ids(findings, "NET-001") == [
        "Namespace/-/open"
    ]  # 'shop' is protected, 'empty' has no workloads


def service(name: str, service_type: str) -> Obj:
    return {
        "kind": "Service",
        "metadata": {"name": name, "namespace": "shop"},
        "spec": {"type": service_type, "ports": [{"port": 80}]},
    }


@pytest.mark.parametrize("service_type", ["LoadBalancer", "NodePort"])
def test_net_002_exposed_service(service_type: str) -> None:
    finding = check(
        "NET-002",
        [service("public", service_type)],
        [service("internal", "ClusterIP")],
        "Service/shop/public",
    )
    assert finding.evidence["type"] == service_type


# --- cost --------------------------------------------------------------------------


def test_cost_001_missing_resources() -> None:
    finding = check("COST-001", [deployment(resources={})], [deployment()], "Deployment/shop/web")
    assert finding.evidence == {
        "missing": {"app": ["requests.cpu", "requests.memory", "limits.memory"]}
    }
    partial = deployment(resources={"requests": {"cpu": "10m", "memory": "16Mi"}})
    assert [f.evidence for f in run_rules([partial]) if f.rule_id == "COST-001"] == [
        {"missing": {"app": ["limits.memory"]}}
    ]


def pod_of(deploy: Obj, name: str) -> Obj:
    return {
        "kind": "Pod",
        "metadata": {
            "name": name,
            "namespace": deploy["metadata"]["namespace"],
            "labels": deploy["spec"]["template"]["metadata"]["labels"],
            "ownerReferences": [{"kind": "ReplicaSet", "name": "rs"}],
        },
        "spec": deploy["spec"]["template"]["spec"],
    }


def pod_metrics(name: str, cpu: str, ns: str = "shop") -> Obj:
    return {
        "kind": "PodMetrics",
        "metadata": {"name": name, "namespace": ns},
        "containers": [{"name": "app", "usage": {"cpu": cpu}}],
    }


def test_cost_002_overprovisioned_cpu() -> None:
    big = deployment(
        resources={"requests": {"cpu": "1", "memory": "64Mi"}, "limits": {"memory": "128Mi"}}
    )
    idle = [
        big,
        pod_of(big, "web-1"),
        pod_of(big, "web-2"),
        pod_metrics("web-1", "20000000n"),
        pod_metrics("web-2", "40m"),
    ]
    busy = [big, pod_of(big, "web-1"), pod_metrics("web-1", "600m")]
    finding = check("COST-002", idle, busy, "Deployment/shop/web")
    assert finding.evidence == {"requestedMillicores": 1000, "usedMillicores": 30, "pods": 2}
    # No metrics, no finding; tiny requests are not worth reporting.
    assert ids(run_rules([big, pod_of(big, "web-1")]), "COST-002") == []
    small = deployment(
        resources={"requests": {"cpu": "50m", "memory": "64Mi"}, "limits": {"memory": "128Mi"}}
    )
    assert (
        ids(run_rules([small, pod_of(small, "web-1"), pod_metrics("web-1", "1m")]), "COST-002")
        == []
    )


@pytest.mark.parametrize(
    ("quantity", "millicores"),
    [
        ("250m", 250.0),
        ("1", 1000.0),
        ("0.5", 500.0),
        (2, 2000.0),
        ("1500000n", 1.5),
        ("250u", 0.25),
        ("abc", None),
        (None, None),
    ],
)
def test_cpu_quantities(quantity: Any, millicores: float | None) -> None:
    assert cpu_millicores(quantity) == millicores


# --- reliability -------------------------------------------------------------------


def test_rel_001_missing_probes() -> None:
    bad = deployment(livenessProbe=None)
    finding = check("REL-001", [bad], [deployment()], "Deployment/shop/web")
    assert finding.evidence == {"missing": {"app": ["livenessProbe"]}}
    cron = {
        "kind": "CronJob",
        "metadata": {"name": "nightly", "namespace": "shop"},
        "spec": {
            "jobTemplate": {
                "spec": {
                    "template": {
                        "spec": {
                            "serviceAccountName": "x",
                            "containers": [{"name": "job", "image": "a:1"}],
                        }
                    }
                }
            }
        },
    }
    assert ids(run_rules([cron]), "REL-001") == []  # batch jobs need no probes


def test_rel_002_single_replica_without_pdb() -> None:
    single = deployment(replicas=1)
    pdb = {
        "kind": "PodDisruptionBudget",
        "metadata": {"name": "web", "namespace": "shop"},
        "spec": {"minAvailable": 1, "selector": {"matchLabels": {"app": "web"}}},
    }
    check("REL-002", [single], [deployment(replicas=2)], "Deployment/shop/web")
    assert ids(run_rules([single, pdb]), "REL-002") == []
    other_namespace = {**pdb, "metadata": {"name": "web", "namespace": "other"}}
    assert ids(run_rules([single, other_namespace]), "REL-002") == ["Deployment/shop/web"]


# --- engine behaviour --------------------------------------------------------------


def test_there_are_fifteen_rules_and_each_has_fallback_text() -> None:
    assert len(RULES) == 15
    for info in RULES.values():
        assert info.title and len(info.explanation) > 40 and len(info.remediation) > 20


def test_pods_owned_by_a_controller_are_reported_once_on_the_controller() -> None:
    bad = deployment(securityContext={"privileged": True})
    findings = run_rules([bad, pod_of(bad, "web-1"), pod_of(bad, "web-2")])
    assert ids(findings, "SEC-001") == ["Deployment/shop/web"]
    standalone = {
        "kind": "Pod",
        "metadata": {"name": "debug", "namespace": "shop"},
        "spec": bad["spec"]["template"]["spec"],
    }
    assert ids(run_rules([standalone]), "SEC-001") == ["Pod/shop/debug"]


def test_system_namespaces_are_skipped_by_default() -> None:
    bad = deployment(namespace="kube-system", securityContext={"privileged": True})
    assert run_rules([namespace("kube-system"), bad]) == []
    assert ids(run_rules([bad], include_system_namespaces=True), "SEC-001") == [
        "Deployment/kube-system/web"
    ]


def test_the_audit_agents_own_namespace_is_not_reported() -> None:
    agent = deployment(name="ape-agent", namespace="ape-system", replicas=1, livenessProbe=None)
    assert run_rules([namespace("ape-system"), agent]) == []


def test_fingerprints_are_stable_and_distinct() -> None:
    bad = deployment(securityContext={"privileged": True})
    first, second = run_rules([bad]), run_rules([bad])
    assert [f.fingerprint for f in first] == [f.fingerprint for f in second]
    assert len({f.fingerprint for f in first}) == len(first)
    ref = ResourceRef("Deployment", "shop", "web")
    assert fingerprint("SEC-001", ref) != fingerprint("SEC-002", ref)
    assert fingerprint("SEC-001", ref) != fingerprint(
        "SEC-001", ResourceRef("Deployment", "shop2", "web")
    )
    assert fingerprint("X", ResourceRef("A", "b", "c")) != fingerprint(
        "X", ResourceRef("A", None, "bc")
    )
    assert len(first[0].fingerprint) == 64


def test_garbage_objects_do_not_crash_the_engine() -> None:
    junk: list[Any] = [
        {},
        {"kind": 5},
        {"kind": "Deployment"},
        {"kind": "Deployment", "metadata": "x", "spec": "y"},
        {
            "kind": "Deployment",
            "metadata": {"name": "d", "namespace": "n"},
            "spec": {"template": {"spec": {"containers": ["x", None, {"name": "c"}]}}},
        },
        {"kind": "Role", "metadata": {"name": "r"}, "rules": ["x", {"verbs": "list"}]},
        {
            "kind": "Service",
            "metadata": {"name": "s"},
            "spec": {"type": "NodePort", "ports": "none"},
        },
        {"kind": "PodMetrics", "metadata": {"name": "m"}, "containers": [None]},
        "not-an-object",
    ]
    run_rules(junk)


# --- the vulnerable test set (task 3.1) --------------------------------------------


def load_vulnerable() -> list[Obj]:
    objects: list[Obj] = []
    for path in sorted((VULNERABLE / "manifests").glob("*.yaml")):
        objects.extend(doc for doc in yaml.safe_load_all(path.read_text()) if doc)
    return objects


def test_vulnerable_set_produces_exactly_the_expected_findings() -> None:
    expected = {
        rule_id: sorted(refs)
        for rule_id, refs in yaml.safe_load((VULNERABLE / "expected.yaml").read_text()).items()
    }
    actual: dict[str, list[str]] = {}
    for finding in run_rules(load_vulnerable()):
        actual.setdefault(finding.rule_id, []).append(str(finding.resource))
    assert {rule_id: sorted(refs) for rule_id, refs in actual.items()} == expected
    # Every rule except the metrics-based one is exercised by the set.
    assert set(expected) == set(RULES) - {"COST-002"}


def test_negative_control_namespace_is_clean() -> None:
    findings = [
        f
        for f in run_rules(load_vulnerable())
        if f.resource.namespace == "clean-app" or f.resource.name == "clean-app"
    ]
    assert findings == []


def test_rules_still_work_on_the_masked_set() -> None:
    from ape.sanitize import Masker

    planted = (VULNERABLE / "planted-secrets.txt").read_text().split()
    masked = Masker(b"k" * 32).mask(load_vulnerable())
    import json

    text = json.dumps(masked)
    assert not [secret for secret in planted if secret in text]
    assert [(f.rule_id, str(f.resource)) for f in run_rules(masked)] == [
        (f.rule_id, str(f.resource)) for f in run_rules(load_vulnerable())
    ]
