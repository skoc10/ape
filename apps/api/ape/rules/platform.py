"""Well-known platform components whose broad access is part of their design.

An operator such as Argo CD or cert-manager cannot work without wide RBAC, and a node
exporter cannot work without access to the host. Reporting these next to the customer's
own mistakes buries the findings somebody can act on. They are still reported, because
the access is real, but rated low and labelled with the component they belong to.

The match is on the object's name only. That is a convention, not a proof: an object that
merely carries one of these names gets the same treatment. This is why such findings are
never dropped, only rated lower.
"""

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

_SEPARATORS = ("-", ":", ".", "_")


def _has_prefix(name: str, prefix: str) -> bool:
    return name == prefix or any(name.startswith(prefix + s) for s in _SEPARATORS)


def platform_component(rule_id: str, resource: ResourceRef) -> str | None:
    """The component a by-design finding belongs to, or None for a regular finding."""
    if rule_id not in BY_DESIGN_RULES:
        return None
    name = resource.name.lower()
    for component, prefixes in _COMPONENTS:
        if any(_has_prefix(name, prefix) for prefix in prefixes):
            return component
    return None
