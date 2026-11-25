package collect

import "k8s.io/apimachinery/pkg/runtime/schema"

// Resource describes one collectable kind.
type Resource struct {
	Kind       string
	GVR        schema.GroupVersionResource
	Namespaced bool
	// Optional resources are skipped silently when the API is not served
	// (metrics-server is not installed in every cluster).
	Optional bool
}

func gvr(group, version, resource string) schema.GroupVersionResource {
	return schema.GroupVersionResource{Group: group, Version: version, Resource: resource}
}

// MetricsGroupVersion is the API that backs the optional usage data.
const MetricsGroupVersion = "metrics.k8s.io/v1beta1"

// Resources is the complete, fixed list of what the agent can collect, in
// collection order. A collect_request can narrow it down but never extend it.
var Resources = []Resource{
	// Workloads
	{Kind: "Pod", GVR: gvr("", "v1", "pods"), Namespaced: true},
	{Kind: "Deployment", GVR: gvr("apps", "v1", "deployments"), Namespaced: true},
	{Kind: "StatefulSet", GVR: gvr("apps", "v1", "statefulsets"), Namespaced: true},
	{Kind: "DaemonSet", GVR: gvr("apps", "v1", "daemonsets"), Namespaced: true},
	{Kind: "Job", GVR: gvr("batch", "v1", "jobs"), Namespaced: true},
	{Kind: "CronJob", GVR: gvr("batch", "v1", "cronjobs"), Namespaced: true},
	// RBAC
	{Kind: "ServiceAccount", GVR: gvr("", "v1", "serviceaccounts"), Namespaced: true},
	{Kind: "Role", GVR: gvr("rbac.authorization.k8s.io", "v1", "roles"), Namespaced: true},
	{Kind: "ClusterRole", GVR: gvr("rbac.authorization.k8s.io", "v1", "clusterroles")},
	{Kind: "RoleBinding", GVR: gvr("rbac.authorization.k8s.io", "v1", "rolebindings"), Namespaced: true},
	{Kind: "ClusterRoleBinding", GVR: gvr("rbac.authorization.k8s.io", "v1", "clusterrolebindings")},
	// Network
	{Kind: "Namespace", GVR: gvr("", "v1", "namespaces")},
	{Kind: "Service", GVR: gvr("", "v1", "services"), Namespaced: true},
	{Kind: "Ingress", GVR: gvr("networking.k8s.io", "v1", "ingresses"), Namespaced: true},
	{Kind: "NetworkPolicy", GVR: gvr("networking.k8s.io", "v1", "networkpolicies"), Namespaced: true},
	// Capacity
	{Kind: "Node", GVR: gvr("", "v1", "nodes")},
	{Kind: "ResourceQuota", GVR: gvr("", "v1", "resourcequotas"), Namespaced: true},
	{Kind: "LimitRange", GVR: gvr("", "v1", "limitranges"), Namespaced: true},
	{Kind: "PodDisruptionBudget", GVR: gvr("policy", "v1", "poddisruptionbudgets"), Namespaced: true},
	// Usage (only with metrics-server)
	{Kind: "PodMetrics", GVR: gvr("metrics.k8s.io", "v1beta1", "pods"), Namespaced: true, Optional: true},
	{Kind: "NodeMetrics", GVR: gvr("metrics.k8s.io", "v1beta1", "nodes"), Optional: true},
}
