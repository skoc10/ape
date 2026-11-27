"""Fixes for Helm releases: a values file is only changed with evidence for file and key.

The fixtures have the shape of the first real repository and cluster this ran against.
"""

import io
import zipfile
from datetime import UTC, datetime
from typing import Any

import pytest
import yaml

from ape.github import helm
from ape.github.pull_request import plan_pull_request
from ape.reports.fixes import build_fix_bundle

NOW = datetime(2026, 11, 27, 12, 0, tzinfo=UTC)
Obj = dict[str, Any]


def workload(
    kind: str,
    namespace: str,
    name: str,
    chart: str,
    instance: str,
    containers: list[str],
    component: str | None = None,
    init: list[str] | None = None,
) -> Obj:
    labels = {
        "helm.sh/chart": chart,
        "app.kubernetes.io/instance": instance,
        "app.kubernetes.io/managed-by": "Helm",
    }
    if component:
        labels["app.kubernetes.io/component"] = component
    pod_spec: Obj = {"containers": [{"name": c} for c in containers]}
    if init:
        pod_spec["initContainers"] = [{"name": c} for c in init]
    return {
        "kind": kind,
        "metadata": {"name": name, "namespace": namespace, "labels": labels},
        "spec": {"template": {"spec": pod_spec}},
    }


OBJECTS = [
    workload(
        "Deployment", "argocd", "argocd-redis", "argo-cd-10.4.0", "argocd", ["redis"], "redis"
    ),
    workload(
        "Deployment",
        "argocd",
        "argocd-dex-server",
        "argo-cd-10.4.0",
        "argocd",
        ["dex-server"],
        "dex-server",
        ["copyutil"],
    ),
    workload(
        "Deployment",
        "cert-manager",
        "cert-manager",
        "cert-manager-v1.18.2",
        "cert-manager",
        ["cert-manager-controller"],
        "controller",
    ),
    workload(
        "Deployment",
        "monitoring",
        "kube-prometheus-stack-grafana",
        "grafana-13.2.2",
        "kube-prometheus-stack",
        ["grafana-sc-dashboard", "grafana-sc-datasources", "grafana"],
        None,
        ["init-chown-data"],
    ),
    workload(
        "DaemonSet",
        "monitoring",
        "kube-prometheus-stack-prometheus-node-exporter",
        "prometheus-node-exporter-4.56.3",
        "kube-prometheus-stack",
        ["node-exporter"],
        "metrics",
    ),
    workload(
        "Deployment",
        "monitoring",
        "opentelemetry-collector",
        "opentelemetry-collector-0.172.1",
        "opentelemetry-collector",
        ["opentelemetry-collector"],
        "standalone-collector",
    ),
    workload("Deployment", "volobox-platform", "site", "volobox-0.1.0", "site", ["volobox"]),
    {
        "kind": "Deployment",
        "metadata": {"name": "handwritten", "namespace": "shop"},
        "spec": {"template": {"spec": {"containers": [{"name": "app"}]}}},
    },
]
INDEX = {(o["kind"], o["metadata"]["namespace"], o["metadata"]["name"]): o for o in OBJECTS}

CERT_MANAGER_APP = """\
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: cert-manager
  namespace: argocd
spec:
  project: default
  sources:
    - repoURL: https://charts.jetstack.io
      chart: cert-manager
      targetRevision: v1.18.2
      helm:
        valueFiles:
          - $values/platform/gitops/addons/cert-manager/values.yaml
    - repoURL: https://github.com/acme/devops.git
      targetRevision: ${ADDONS_GIT_REVISION}
      ref: values
  destination:
    server: https://kubernetes.default.svc
    namespace: cert-manager
"""

KPS_VALUES = """\
# Monitoring stack. Sized for the system pool.
grafana:
  adminUser: admin   # rotated by the bootstrap script
  persistence:
    enabled: true
prometheus:
  prometheusSpec:
    retention: 15d
"""

ARGOCD_VALUES = """\
global:
  domain: argocd.example.com
redis:
  # Small on purpose: only a cache.
  resources:
    requests:
      memory: 64Mi
"""

SOURCES = {
    "platform/gitops/addons/apps/cert-manager.yaml.tpl": CERT_MANAGER_APP,
    "platform/gitops/addons/cert-manager/values.yaml": "crds:\n  enabled: true\n",
    "platform/gitops/addons/argocd/values.yaml": ARGOCD_VALUES,
    "platform/gitops/observability/kube-prometheus-stack-values.yaml": KPS_VALUES,
    "platform/gitops/observability/otel-collector-values.yaml": "mode: deployment\n",
    "platform/gitops/platform/values/site.yaml": "replicaCount: 1\n",
}


def pod_patch(pod_spec: Obj) -> str:
    return yaml.safe_dump({"spec": {"template": {"spec": pod_spec}}})


def escalation(container: str) -> str:
    return pod_patch(
        {
            "containers": [
                {"name": container, "securityContext": {"allowPrivilegeEscalation": False}}
            ]
        }
    )


def row(rule_id: str, kind: str, namespace: str, name: str, patch: str) -> dict[str, Any]:
    return {
        "fingerprint": f"{rule_id}-{namespace}-{name}",
        "rule_id": rule_id,
        "title": f"Title of {rule_id}",
        "resource_kind": kind,
        "resource_ns": namespace,
        "resource_name": name,
        "patch": patch,
    }


ROWS = [
    row(
        "RBAC-003",
        "Deployment",
        "argocd",
        "argocd-redis",
        pod_patch({"automountServiceAccountToken": False}),
    ),
    row(
        "SEC-004",
        "DaemonSet",
        "monitoring",
        "kube-prometheus-stack-prometheus-node-exporter",
        escalation("node-exporter"),
    ),
    row(
        "SEC-004",
        "Deployment",
        "cert-manager",
        "cert-manager",
        escalation("cert-manager-controller"),
    ),
    # A sidecar: the chart has other keys for it, which are not in the catalogue.
    row(
        "SEC-004",
        "Deployment",
        "monitoring",
        "kube-prometheus-stack-grafana",
        escalation("grafana-sc-dashboard"),
    ),
    # A probe needs a path and a port, and every chart names that differently.
    row(
        "REL-001",
        "Deployment",
        "argocd",
        "argocd-dex-server",
        pod_patch(
            {"containers": [{"name": "dex-server", "livenessProbe": {"httpGet": {"path": "/"}}}]}
        ),
    ),
    # Translatable, but its values file has a name nothing ties to the release.
    row(
        "SEC-004",
        "Deployment",
        "monitoring",
        "opentelemetry-collector",
        escalation("opentelemetry-collector"),
    ),
    # An in-house chart: nothing is known about its keys.
    row("SEC-004", "Deployment", "volobox-platform", "site", escalation("volobox")),
    row("SEC-004", "Deployment", "shop", "handwritten", escalation("app")),
]


def plan(sources: dict[str, str] = SOURCES, rows: list[dict[str, Any]] = ROWS) -> Any:
    return plan_pull_request(
        sources, rows, cluster_name="prod", summary="", score=64, now=NOW, objects=INDEX
    )


def test_release_is_read_from_the_labels_charts_set() -> None:
    release = helm.release_of(INDEX[("Deployment", "cert-manager", "cert-manager")])
    assert release == helm.Release(
        "cert-manager", "v1.18.2", "cert-manager", "cert-manager", "controller"
    )
    assert helm.release_of(INDEX[("Deployment", "shop", "handwritten")]) is None
    assert helm.release_of(None) is None
    # A chart label without a version, or no release name: not enough to act on.
    broken = workload("Deployment", "x", "y", "just-a-name", "rel", ["c"])
    assert helm.release_of(broken) is None


def test_values_files_are_changed_only_with_evidence_for_file_and_key() -> None:
    result = plan().result
    applied = {str(a.request.target): (a.path, a.rule, a.detail) for a in result.applied}
    assert applied == {
        "Deployment/argocd/argocd-redis": (
            "platform/gitops/addons/argocd/values.yaml",
            "helm_values",
            "chart argo-cd 10.4.0, redis.automountServiceAccountToken (file found by file name)",
        ),
        "DaemonSet/monitoring/kube-prometheus-stack-prometheus-node-exporter": (
            "platform/gitops/observability/kube-prometheus-stack-values.yaml",
            "helm_values",
            "chart kube-prometheus-stack with prometheus-node-exporter 4.56.3, "
            "prometheus-node-exporter.containerSecurityContext; key checked against chart "
            "version 6.0.0 (file found by file name)",
        ),
        "Deployment/cert-manager/cert-manager": (
            "platform/gitops/addons/cert-manager/values.yaml",
            "helm_values",
            "chart cert-manager v1.18.2, containerSecurityContext "
            "(file found by Argo CD Application)",
        ),
    }
    assert set(result.files) == {a[0] for a in applied.values()}

    argocd = result.files["platform/gitops/addons/argocd/values.yaml"]
    assert argocd == ARGOCD_VALUES + "  automountServiceAccountToken: false\n"
    kps = result.files["platform/gitops/observability/kube-prometheus-stack-values.yaml"]
    # Comments and everything that was there stay; the new key is added below.
    assert kps.startswith(KPS_VALUES)
    assert yaml.safe_load(kps)["prometheus-node-exporter"] == {
        "containerSecurityContext": {"allowPrivilegeEscalation": False}
    }
    cert_manager = yaml.safe_load(result.files["platform/gitops/addons/cert-manager/values.yaml"])
    assert cert_manager == {
        "crds": {"enabled": True},
        "containerSecurityContext": {"allowPrivilegeEscalation": False},
    }


def test_everything_without_evidence_stays_manual_and_says_why() -> None:
    result = plan().result
    reasons = {str(m.request.target): m.reason for m in result.manual}
    assert set(reasons) == {
        "Deployment/monitoring/kube-prometheus-stack-grafana",
        "Deployment/argocd/argocd-dex-server",
        "Deployment/monitoring/opentelemetry-collector",
        "Deployment/volobox-platform/site",
        "Deployment/shop/handwritten",
    }
    for target in (
        "Deployment/monitoring/kube-prometheus-stack-grafana",
        "Deployment/argocd/argocd-dex-server",
    ):
        assert "the patch changes fields that have no verified value key" in reasons[target]
    assert reasons["Deployment/volobox-platform/site"] == (
        "rendered by Helm release site (chart volobox 0.1.0); no verified value keys are "
        "known for this workload"
    )
    # Not a Helm resource: the reason is the one it had before.
    assert reasons["Deployment/shop/handwritten"].startswith("no manifest for this resource")

    # Known keys but no file: the manual entry shows the values to set, not a patch that
    # cannot be applied to a chart.
    otel = next(m for m in result.manual if m.request.target.name == "opentelemetry-collector")
    assert "no values file for it was found in the repository" in otel.reason
    assert "chart opentelemetry-collector 0.172.1, securityContext" in otel.reason
    assert yaml.safe_load(otel.request.patch) == {
        "securityContext": {"allowPrivilegeEscalation": False}
    }
    # Untouched: the file that merely looks related.
    assert "platform/gitops/observability/otel-collector-values.yaml" not in result.files
    assert "platform/gitops/platform/values/site.yaml" not in result.files


def test_pull_request_text_names_chart_and_keys() -> None:
    body = plan().body
    assert "Helm values: chart argo-cd 10.4.0, redis.automountServiceAccountToken" in body
    assert "rendered by Helm release site (chart volobox 0.1.0)" in body
    assert "Set these values for the release" in body


@pytest.mark.parametrize(
    ("patch", "expected"),
    [
        (
            pod_patch(
                {
                    "automountServiceAccountToken": False,
                    "securityContext": {"runAsNonRoot": True},
                    "containers": [
                        {
                            "name": "redis",
                            "securityContext": {"allowPrivilegeEscalation": False},
                            "resources": {"limits": {"memory": "128Mi"}},
                        }
                    ],
                }
            ),
            {
                ("redis", "automountServiceAccountToken"): False,
                ("redis", "securityContext"): {"runAsNonRoot": True},
                ("redis", "containerSecurityContext"): {"allowPrivilegeEscalation": False},
                ("redis", "resources"): {"limits": {"memory": "128Mi"}},
            },
        ),
        # Any part without a key stops the whole translation.
        (pod_patch({"automountServiceAccountToken": False, "hostNetwork": False}), None),
        (
            pod_patch({"containers": [{"name": "redis", "env": [{"name": "A", "value": "b"}]}]}),
            None,
        ),
        (
            pod_patch(
                {"containers": [{"name": "metrics", "securityContext": {"privileged": False}}]}
            ),
            None,
        ),
        (
            pod_patch(
                {
                    "containers": [
                        {"name": "redis", "securityContext": {"privileged": False}},
                        {"name": "metrics", "securityContext": {"privileged": False}},
                    ]
                }
            ),
            None,
        ),
        (
            pod_patch(
                {"initContainers": [{"name": "x", "securityContext": {"privileged": False}}]}
            ),
            None,
        ),
        (yaml.safe_dump({"spec": {"replicas": 2}}), None),
        (
            yaml.safe_dump(
                {
                    "spec": {
                        "replicas": 2,
                        "template": {"spec": {"automountServiceAccountToken": False}},
                    }
                }
            ),
            None,
        ),
        (
            yaml.safe_dump(
                {
                    "metadata": {"labels": {"a": "b"}},
                    "spec": {"template": {"spec": {"automountServiceAccountToken": False}}},
                }
            ),
            {("redis", "automountServiceAccountToken"): False},
        ),
        ("- a\n- list\n", None),
        (pod_patch({"automountServiceAccountToken": "false"}), None),
    ],
)
def test_translation_is_all_or_nothing(patch: str, expected: dict[helm.Path, Any] | None) -> None:
    keys = helm.CATALOGUE["argo-cd"][0]
    assert helm.translate(yaml.safe_load(patch), "Deployment", keys) == expected


def test_a_field_without_a_verified_key_is_not_translated() -> None:
    node_exporter = helm.CATALOGUE["prometheus-node-exporter"][0]
    # The pod's automount flag is computed from two values in that chart.
    assert (
        helm.translate(
            yaml.safe_load(pod_patch({"automountServiceAccountToken": False})),
            "DaemonSet",
            node_exporter,
        )
        is None
    )
    ingress = helm.CATALOGUE["ingress-nginx"][0]
    assert helm.translate(yaml.safe_load(escalation("controller")), "Deployment", ingress) is None
    # Kinds whose pod spec is elsewhere are left alone.
    assert (
        helm.translate(yaml.safe_load(escalation("node-exporter")), "CronJob", node_exporter)
        is None
    )


def test_the_catalogue_is_only_used_for_the_workload_it_was_written_for() -> None:
    # Another component of the same chart, and a workload whose main container was renamed.
    webhook = workload(
        "Deployment",
        "cert-manager",
        "cert-manager-webhook",
        "cert-manager-v1.18.2",
        "cert-manager",
        ["cert-manager-webhook"],
        "webhook",
    )
    release = helm.release_of(webhook)
    assert release is not None and helm.keys_for(release, webhook) is None
    renamed = workload(
        "Deployment",
        "ingress-nginx",
        "c",
        "ingress-nginx-4.13.3",
        "ingress-nginx",
        ["nginx"],
        "controller",
    )
    release = helm.release_of(renamed)
    assert release is not None and helm.keys_for(release, renamed) is None


def test_ambiguous_or_unsafe_values_files_are_left_alone() -> None:
    redis_only = [ROWS[0]]
    # Two files that could both be the release's values.
    two = {**SOURCES, "other/argocd/values.yaml": "redis: {}\n"}
    manual = plan(two, redis_only).result.manual
    assert [m.reason.split("; ")[1].split(".")[0] for m in manual] == [
        "several values files could belong to it"
    ]
    # The key already holds something of another shape: the file is not what we think.
    odd = {**SOURCES, "platform/gitops/addons/argocd/values.yaml": "redis: external\n"}
    result = plan(odd, redis_only).result
    assert result.files == {} and "already holds a value of another type" in result.manual[0].reason
    # A Kubernetes manifest that happens to be called values.yaml is not a values file.
    manifest = {
        "x/argocd/values.yaml": "apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: a\n",
    }
    assert "no values file" in plan(manifest, redis_only).result.manual[0].reason
    # A templated file is never rewritten.
    templated = {"x/argocd/values.yaml": "redis:\n  image: {{ .Values.image }}\n"}
    assert plan(templated, redis_only).result.files == {}
    # An Application with two value files: which one carries the setting is not ours to pick.
    two_files = CERT_MANAGER_APP.replace(
        "          - $values/platform/gitops/addons/cert-manager/values.yaml\n",
        "          - $values/a/base/values.yaml\n          - $values/a/prod/values.yaml\n",
    )
    sources = {
        "apps/cm.yaml": two_files,
        "a/base/values.yaml": "a: 1\n",
        "a/prod/values.yaml": "b: 2\n",
    }
    assert plan(sources, [ROWS[2]]).result.files == {}


def test_a_fitting_file_name_is_no_evidence_when_the_values_are_known_to_be_elsewhere() -> None:
    cert_manager = [ROWS[2]]
    named = {"x/cert-manager/values.yaml": "replicaCount: 1\n"}
    assert plan(named, cert_manager).result.files != {}  # the name alone, nothing against it

    def reason(sources: dict[str, str]) -> str:
        result = plan(sources, cert_manager).result
        assert result.files == {}
        return result.manual[0].reason

    # The Application reads its values from a path that is not in this repository. A bare
    # values.yaml at the root shares nothing with it but the file name.
    elsewhere = CERT_MANAGER_APP.replace("platform/gitops/addons", "envs/prod")
    other_chart = {"values.yaml": "replicaCount: 2\n", "Chart.yaml": "name: shop\n"}
    assert "was not found in this repository" in reason({"apps/cm.yaml": elsewhere, **other_chart})
    assert "was not found in this repository" in reason({"apps/cm.yaml": elsewhere, **named})
    # A path without "$ref/" is read from inside the chart package, and ".." leaves the source.
    for entry in ("cert-manager/values.yaml", "$values/../x/cert-manager/values.yaml"):
        inside = CERT_MANAGER_APP.replace(
            "$values/platform/gitops/addons/cert-manager/values.yaml", entry
        )
        assert "was not found in this repository" in reason({"apps/cm.yaml": inside, **named})
    # Two value files, one of them with a fitting name: still not ours to pick.
    two_files = CERT_MANAGER_APP.replace(
        "          - $values/platform/gitops/addons/cert-manager/values.yaml\n",
        "          - $values/x/cert-manager/values.yaml\n          - $values/x/prod.yaml\n",
    )
    assert "exactly one file" in reason({"apps/cm.yaml": two_files, **named, "x/prod.yaml": "a: 1"})
    # A HelmRelease that takes its values from a ConfigMap.
    flux = """\
apiVersion: helm.toolkit.fluxcd.io/v2
kind: HelmRelease
metadata: {name: cert-manager, namespace: cert-manager}
spec:
  chart: {spec: {chart: cert-manager}}
  valuesFrom: [{kind: ConfigMap, name: v}]
"""
    assert "does not keep its values inline" in reason({"clusters/cm.yaml": flux, **named})


def test_the_values_file_of_a_wrapper_chart_is_not_written_at_the_top_level() -> None:
    cert_manager = [ROWS[2]]
    values = {"x/cert-manager/values.yaml": "cert-manager:\n  replicaCount: 1\n"}

    def chart(text: str) -> Any:
        return plan({**values, "x/cert-manager/Chart.yaml": text}, cert_manager).result

    # The chart passes these values on to its dependency, below the dependency's name.
    wrapper = chart(
        "apiVersion: v2\nname: cert-manager\nversion: 0.1.0\n"
        "dependencies:\n  - name: cert-manager\n    version: v1.18.2\n"
    )
    assert wrapper.files == {} and "belongs to another chart" in wrapper.manual[0].reason
    aliased = chart(
        "name: platform-certs\ndependencies:\n  - {name: upstream, alias: cert-manager}\n"
    )
    assert aliased.files == {}
    assert chart("name: something-else\nversion: 1.0.0\n").files == {}
    assert chart("name: [not, a, chart\n").files == {}
    # A copy of the chart itself kept in the repository: its own values file.
    assert chart("apiVersion: v2\nname: cert-manager\nversion: v1.18.2\n").files != {}


def test_a_dependency_chart_needs_proof_of_its_parent() -> None:
    node_exporter = [ROWS[1]]
    # Named after the release, and the file shows the parent's layout.
    obj = dict(INDEX[("DaemonSet", "monitoring", "kube-prometheus-stack-prometheus-node-exporter")])
    obj["metadata"] = {
        **obj["metadata"],
        "labels": {**obj["metadata"]["labels"], "app.kubernetes.io/instance": "monitoring"},
    }
    index = {
        **INDEX,
        ("DaemonSet", "monitoring", "kube-prometheus-stack-prometheus-node-exporter"): obj,
    }

    def run(sources: dict[str, str]) -> Any:
        return plan_pull_request(
            sources, node_exporter, cluster_name="p", summary="", score=None, now=NOW, objects=index
        ).result

    with_key = run(
        {"x/monitoring-values.yaml": "prometheus-node-exporter:\n  hostNetwork: false\n"}
    )
    assert yaml.safe_load(with_key.files["x/monitoring-values.yaml"]) == {
        "prometheus-node-exporter": {
            "hostNetwork": False,
            "containerSecurityContext": {"allowPrivilegeEscalation": False},
        }
    }
    # Named after the release only: it could be the stack's file or the exporter's own.
    without = run({"x/monitoring-values.yaml": "replicas: 1\n"})
    assert without.files == {} and "no values file" in without.manual[0].reason


def test_flux_helm_release_with_inline_values_is_edited_in_place() -> None:
    release = """\
apiVersion: helm.toolkit.fluxcd.io/v2
kind: HelmRelease
metadata:
  name: argocd
  namespace: argocd
spec:
  interval: 10m
  chart:
    spec:
      chart: argo-cd
      version: 10.4.0
  values:
    redis:
      resources: {}
"""
    result = plan({"clusters/prod/argocd.yaml": release}, [ROWS[0]]).result
    assert [a.detail for a in result.applied] == [
        "chart argo-cd 10.4.0, redis.automountServiceAccountToken (file found by Flux HelmRelease)"
    ]
    edited = yaml.safe_load(result.files["clusters/prod/argocd.yaml"])
    assert edited["spec"]["values"]["redis"] == {
        "resources": {},
        "automountServiceAccountToken": False,
    }
    assert edited["kind"] == "HelmRelease" and edited["spec"]["interval"] == "10m"
    # valuesFrom points at ConfigMaps and Secrets: nothing in the repository to edit.
    external = release.replace(
        "  values:\n", "  valuesFrom:\n    - kind: ConfigMap\n      name: v\n  values:\n"
    )
    assert plan({"clusters/prod/argocd.yaml": external}, [ROWS[0]]).result.files == {}


def test_without_the_audited_objects_nothing_changes() -> None:
    """A missing snapshot gives the old result, never a guess."""
    result = plan_pull_request(
        SOURCES, ROWS, cluster_name="p", summary="", score=None, now=NOW
    ).result
    assert result.files == {} and result.applied == []
    assert all(m.reason.startswith("no manifest for this resource") for m in result.manual)


def test_fix_bundle_carries_a_values_overlay_per_release() -> None:
    bundle = build_fix_bundle(ROWS, cluster_name="prod", now=NOW, objects=INDEX)
    with zipfile.ZipFile(io.BytesIO(bundle.archive)) as archive:
        files = {name: archive.read(name).decode() for name in archive.namelist()}
    overlays = {
        name: yaml.safe_load(text)
        for name, text in files.items()
        if name.startswith("helm-values/")
    }
    assert overlays == {
        "helm-values/argocd-argocd.yaml": {"redis": {"automountServiceAccountToken": False}},
        "helm-values/cert-manager-cert-manager.yaml": {
            "containerSecurityContext": {"allowPrivilegeEscalation": False}
        },
        "helm-values/monitoring-kube-prometheus-stack.yaml": {
            "prometheus-node-exporter": {
                "containerSecurityContext": {"allowPrivilegeEscalation": False}
            }
        },
        "helm-values/monitoring-opentelemetry-collector.yaml": {
            "securityContext": {"allowPrivilegeEscalation": False}
        },
    }
    assert files["helm-values/argocd-argocd.yaml"].startswith(
        "# Values for Helm release argocd in namespace argocd.\n"
    )
    readme = files["README.md"]
    assert (
        "## Helm releases" in readme
        and "helm-values/monitoring-opentelemetry-collector.yaml" in readme
    )
    # The raw patches are still there, and without objects there are no overlays.
    assert "patches/argocd/deployment-argocd-redis.yaml" in files
    plain = build_fix_bundle(ROWS, cluster_name="prod", now=NOW)
    assert plain.helm_values == []


def test_catalogue_entries_are_pinned() -> None:
    """Every key here was read off the chart's template. A change has to be a decision."""
    pinned = {
        chart: [
            (
                k.component,
                k.main_container,
                k.container_security_context,
                k.pod_security_context,
                k.resources,
                k.automount,
            )
            for k in entries
        ]
        for chart, entries in helm.CATALOGUE.items()
    }
    assert pinned == {
        "opentelemetry-collector": [
            (
                None,
                "opentelemetry-collector",
                ("securityContext",),
                ("podSecurityContext",),
                ("resources",),
                ("serviceAccount", "automountServiceAccountToken"),
            ),
        ],
        "prometheus-node-exporter": [
            (
                None,
                "node-exporter",
                ("containerSecurityContext",),
                ("securityContext",),
                ("resources",),
                None,
            ),
        ],
        "grafana": [
            (
                None,
                "grafana",
                ("containerSecurityContext",),
                ("securityContext",),
                ("resources",),
                ("automountServiceAccountToken",),
            ),
        ],
        "cert-manager": [
            (
                "controller",
                "cert-manager-controller",
                ("containerSecurityContext",),
                ("securityContext",),
                ("resources",),
                ("automountServiceAccountToken",),
            ),
        ],
        "ingress-nginx": [
            (
                "controller",
                "controller",
                None,
                ("controller", "podSecurityContext"),
                ("controller", "resources"),
                ("serviceAccount", "automountServiceAccountToken"),
            ),
        ],
        "argo-cd": [
            (
                "redis",
                "redis",
                ("redis", "containerSecurityContext"),
                ("redis", "securityContext"),
                ("redis", "resources"),
                ("redis", "automountServiceAccountToken"),
            ),
            (
                "dex-server",
                "dex-server",
                ("dex", "containerSecurityContext"),
                None,
                ("dex", "resources"),
                ("dex", "automountServiceAccountToken"),
            ),
            (
                "notifications-controller",
                "notifications-controller",
                ("notifications", "containerSecurityContext"),
                None,
                ("notifications", "resources"),
                ("notifications", "automountServiceAccountToken"),
            ),
            ("server", "server", None, None, None, ("server", "automountServiceAccountToken")),
        ],
    }
    assert helm.PARENTS == {
        "kube-prometheus-stack": {
            "grafana": "grafana",
            "prometheus-node-exporter": "prometheus-node-exporter",
        }
    }
