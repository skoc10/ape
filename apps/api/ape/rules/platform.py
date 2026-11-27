"""Well-known platform components whose broad access is part of their design.

An operator such as Argo CD or cert-manager cannot work without wide RBAC, and a node
exporter cannot work without access to the host. Reporting these next to the customer's
own mistakes buries the findings somebody can act on. They are still reported, because
the access is real, but rated low and labelled with the component they belong to.

The match starts from the object's name. That is a convention, not a proof, so such
findings are never dropped, only rated lower, and the name alone is not enough where the
object itself says who gets the access:

* a cluster-admin binding (RBAC-002) is only rated down when every subject is a service
  account of the same component, or of a system namespace. A binding that hands
  cluster-admin to a user, a group or somebody else's service account is the customer's
  own decision whatever it is called; the few bindings a cloud provider creates for
  users and groups are listed by their exact name together with the subjects the
  provider puts there, so an extra subject added to one of them is reported in full;
* the cloud providers' prefixes are short and generic ("aws", "azure", "gke"), and what
  the provider manages is cluster-scoped or lives in its own namespaces. A namespaced
  object elsewhere that merely starts with such a prefix is a regular finding.
"""

import re
from typing import Any

from ape.rules.model import ResourceRef

# Rules about access that these components need by design. Everything else (probes,
# resource requests, network policies, image tags) is configurable by the customer
# through the component's chart and stays at its normal severity.
BY_DESIGN_RULES = frozenset({"RBAC-001", "RBAC-002", "RBAC-004", "SEC-002"})

# (component, name prefixes). A prefix matches the whole name or a name that continues
# with a separator, so "argocd" matches "argocd-server" but not "argocdx".
_COMPONENTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Argo CD", ("argocd", "argo-cd")),
    ("Flux", ("flux", "source-controller", "kustomize-controller", "helm-controller")),
    ("cert-manager", ("cert-manager",)),
    ("ingress-nginx", ("ingress-nginx", "nginx-ingress")),
    ("Traefik", ("traefik",)),
    (
        "Prometheus stack",
        ("kube-prometheus-stack", "prometheus-operator", "kube-state-metrics", "prometheus"),
    ),
    ("Grafana", ("grafana",)),
    ("External Secrets", ("external-secrets",)),
    (
        "Secrets Store CSI driver",
        (
            "secrets-store-csi",
            "secretproviderclasses",
            "secretproviderrotation",
            "secretprovidersyncing",
        ),
    ),
    ("ExternalDNS", ("external-dns",)),
    ("Velero", ("velero",)),
    ("Istio", ("istio", "istiod")),
    ("Cilium", ("cilium",)),
    ("Calico", ("calico", "tigera")),
    ("Karpenter", ("karpenter",)),
    ("Cluster Autoscaler", ("cluster-autoscaler",)),
    ("KEDA", ("keda",)),
    ("Kyverno", ("kyverno",)),
    ("Gatekeeper", ("gatekeeper",)),
    ("CloudNativePG", ("cnpg", "cloudnative-pg")),
    ("Azure Kubernetes Service", ("aks", "azure", "omsagent", "ama")),
    ("Amazon EKS", ("eks", "aws", "ebs-csi", "efs-csi", "vpc-resource-controller")),
    ("Google Kubernetes Engine", ("gke", "gce")),
)

# Components of the cloud provider itself, as opposed to software the customer installs.
_PROVIDERS = frozenset({"Azure Kubernetes Service", "Amazon EKS", "Google Kubernetes Engine"})

# Namespaces a provider (or Kubernetes) manages. Extend it when a provider adds one.
_PROVIDER_NAMESPACES = frozenset(
    {
        "kube-system",
        "kube-public",
        "kube-node-lease",
        "gatekeeper-system",
        "calico-system",
        "tigera-operator",
        "gke-managed-system",
        "gke-gmp-system",
        "gmp-system",
        "amazon-cloudwatch",
        "amazon-guardduty",
        "app-routing-system",
    }
)

# cluster-admin bindings to users or groups that a provider creates on every cluster, with
# the subjects the provider puts there. The name is not enough here either: anybody who
# may edit bindings can add a subject to an existing one.
_AKS_LOCAL_ACCOUNTS = frozenset({"clusterAdmin", "clusterUser"})


def _aks_local_accounts(subject: dict[str, Any]) -> bool:
    return subject.get("kind") == "User" and subject.get("name") in _AKS_LOCAL_ACCOUNTS


_OBJECT_ID = re.compile(r"^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$", re.IGNORECASE)


def _directory_group(subject: dict[str, Any]) -> bool:
    # The admin groups of the customer's directory, which AKS binds by their object id.
    # Any other group is somebody's addition: "system:authenticated" here would hand
    # cluster-admin to every account that can log in.
    name = subject.get("name")
    return subject.get("kind") == "Group" and isinstance(name, str) and bool(_OBJECT_ID.match(name))


_PROVIDER_ADMIN_BINDINGS = {
    "aks-cluster-admin-binding": _aks_local_accounts,
    "aks-cluster-admin-binding-aad": _directory_group,
}

_SEPARATORS = ("-", ":", ".", "_")


def _has_prefix(name: str, prefix: str) -> bool:
    return name == prefix or any(name.startswith(prefix + s) for s in _SEPARATORS)


def _subject_of_component(subject: Any, component: str, prefixes: tuple[str, ...]) -> bool:
    """True for a service account that is part of the component the binding is named after."""
    if not isinstance(subject, dict) or subject.get("kind") != "ServiceAccount":
        return False
    namespace = str(subject.get("namespace") or "").lower()
    if namespace in _PROVIDER_NAMESPACES:
        return True
    if component in _PROVIDERS:
        return False
    name = str(subject.get("name") or "").lower()
    return any(_has_prefix(name, p) or _has_prefix(namespace, p) for p in prefixes)


def platform_component(
    rule_id: str, resource: ResourceRef, evidence: dict[str, Any] | None = None
) -> str | None:
    """The component a by-design finding belongs to, or None for a regular finding.

    ``evidence`` is the finding's evidence; for RBAC-002 it carries the binding's subjects.
    """
    if rule_id not in BY_DESIGN_RULES:
        return None
    name = resource.name.lower()
    for component, prefixes in _COMPONENTS:
        if not any(_has_prefix(name, prefix) for prefix in prefixes):
            continue
        if (
            component in _PROVIDERS
            and resource.namespace is not None
            and resource.namespace not in _PROVIDER_NAMESPACES
        ):
            return None
        if rule_id == "RBAC-002":
            subjects = (evidence or {}).get("subjects")
            if not isinstance(subjects, list) or not subjects:
                return None
            expected = _PROVIDER_ADMIN_BINDINGS.get(name)
            if expected is not None:
                if not all(isinstance(s, dict) and expected(s) for s in subjects):
                    return None
            elif not all(_subject_of_component(s, component, prefixes) for s in subjects):
                return None
        return component
    return None
