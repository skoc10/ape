import hashlib
from dataclasses import dataclass, field
from typing import Any, Literal

Category = Literal["security", "rbac", "network", "cost", "reliability"]
Severity = Literal["critical", "high", "medium", "low", "info"]


@dataclass(frozen=True, slots=True)
class ResourceRef:
    kind: str
    namespace: str | None
    name: str

    def __str__(self) -> str:
        return f"{self.kind}/{self.namespace or '-'}/{self.name}"


def fingerprint(rule_id: str, resource: ResourceRef) -> str:
    """Stable identity of a finding across runs: same rule, same resource."""
    raw = "\x1f".join([rule_id, resource.kind, resource.namespace or "", resource.name])
    return hashlib.sha256(raw.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class RuleInfo:
    """Static description of a rule, also the fallback text when no LLM is available."""

    id: str
    category: Category
    severity: Severity
    title: str
    explanation: str
    remediation: str


@dataclass(frozen=True, slots=True)
class Finding:
    rule_id: str
    category: Category
    severity: Severity
    title: str
    resource: ResourceRef
    # Small, masked excerpt that proves the finding (container names, field values).
    evidence: dict[str, Any] = field(default_factory=dict)

    @property
    def fingerprint(self) -> str:
        return fingerprint(self.rule_id, self.resource)


def _rule(
    rule_id: str,
    category: Category,
    severity: Severity,
    title: str,
    explanation: str,
    remediation: str,
) -> RuleInfo:
    return RuleInfo(rule_id, category, severity, title, explanation, remediation)


RULES: dict[str, RuleInfo] = {
    r.id: r
    for r in [
        _rule(
            "SEC-001",
            "security",
            "critical",
            "Privileged container",
            "A privileged container has almost the same access to the node as a root process "
            "on the host. Anyone who takes over this container controls the node.",
            "Remove securityContext.privileged. If a specific kernel feature is needed, add "
            "only that capability under securityContext.capabilities.add.",
        ),
        _rule(
            "SEC-002",
            "security",
            "high",
            "Access to the host (network, PID, IPC or hostPath)",
            "The workload shares the node's network or process namespace, or mounts a path of "
            "the node's file system. This breaks the isolation between the pod and the node.",
            "Remove hostNetwork, hostPID and hostIPC, and replace hostPath volumes with a "
            "PersistentVolumeClaim, ConfigMap, Secret or emptyDir.",
        ),
        _rule(
            "SEC-003",
            "security",
            "medium",
            "Container may run as root",
            "Nothing prevents the container from running as the root user. A vulnerability in "
            "the application then immediately gives root inside the container.",
            "Set securityContext.runAsNonRoot: true and a non-zero runAsUser, on the pod or on "
            "each container.",
        ),
        _rule(
            "SEC-004",
            "security",
            "medium",
            "Privilege escalation is not disabled",
            "allowPrivilegeEscalation is not set to false, so a process can gain more "
            "privileges than its parent, for example through setuid binaries.",
            "Set securityContext.allowPrivilegeEscalation: false on every container.",
        ),
        _rule(
            "SEC-005",
            "security",
            "medium",
            "Mutable or missing image tag",
            "The image is referenced with :latest or without a tag. The same manifest can "
            "start different software tomorrow, and a rollback cannot be reproduced.",
            "Pin every image to a specific version tag, or better, to a digest.",
        ),
        _rule(
            "RBAC-001",
            "rbac",
            "high",
            "Wildcard permissions in a role",
            "The role grants all verbs or all resources with '*'. It also covers resources "
            "that will be added to the cluster later.",
            "List the exact resources and verbs the workload needs instead of '*'.",
        ),
        _rule(
            "RBAC-002",
            "rbac",
            "critical",
            "Binding to cluster-admin",
            "The subject has unrestricted control over the whole cluster, including reading "
            "every secret and changing any workload.",
            "Bind a role with only the required permissions; keep cluster-admin for break-glass "
            "accounts of human administrators.",
        ),
        _rule(
            "RBAC-003",
            "rbac",
            "medium",
            "Default ServiceAccount with a mounted token",
            "The workload runs as the namespace's default ServiceAccount and its API token is "
            "mounted into the pod. Every permission ever given to 'default' is available to it.",
            "Create a dedicated ServiceAccount for the workload, and set "
            "automountServiceAccountToken: false if it does not call the Kubernetes API.",
        ),
        _rule(
            "RBAC-004",
            "rbac",
            "high",
            "Role can list secrets",
            "Listing secrets returns their contents, so this role can read every secret in "
            "its scope, not only the ones the workload uses.",
            "Remove 'list' and 'watch' on secrets; grant 'get' on named secrets with "
            "resourceNames if the workload has to read specific ones.",
        ),
        _rule(
            "NET-001",
            "network",
            "medium",
            "Namespace without a NetworkPolicy",
            "Every pod in this namespace can be reached from any other pod in the cluster, and "
            "can itself connect anywhere.",
            "Add a default-deny NetworkPolicy to the namespace and then allow only the "
            "connections the workloads need.",
        ),
        _rule(
            "NET-002",
            "network",
            "medium",
            "Service exposed outside the cluster",
            "A LoadBalancer or NodePort service is reachable from outside the cluster network, "
            "bypassing the Ingress layer and its TLS and authentication.",
            "Use a ClusterIP service behind an Ingress, or restrict access with "
            "loadBalancerSourceRanges if direct exposure is intended.",
        ),
        _rule(
            "COST-001",
            "cost",
            "low",
            "Container without resource requests or limits",
            "Without requests the scheduler cannot place the pod sensibly, and without a memory "
            "limit one container can starve its neighbours on the node.",
            "Set resources.requests for cpu and memory and resources.limits.memory, based on "
            "observed usage.",
        ),
        _rule(
            "COST-002",
            "cost",
            "low",
            "Requested CPU is far above actual usage",
            "The workload reserves more than three times the CPU it currently uses. Reserved "
            "capacity is paid for even when it sits idle.",
            "Lower resources.requests.cpu towards the observed usage, with headroom for peaks.",
        ),
        _rule(
            "REL-001",
            "reliability",
            "medium",
            "Missing liveness or readiness probe",
            "Without a readiness probe traffic reaches the pod before it can serve it; without "
            "a liveness probe a hung process is never restarted.",
            "Add readinessProbe and livenessProbe to every long-running container.",
        ),
        _rule(
            "REL-002",
            "reliability",
            "low",
            "Single replica without a PodDisruptionBudget",
            "There is one replica and no PodDisruptionBudget: a node drain or a crash takes the "
            "service down completely.",
            "Run at least two replicas and add a PodDisruptionBudget with minAvailable: 1.",
        ),
    ]
}
