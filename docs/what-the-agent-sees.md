# What the audit agent sees, and what it does not

This page is for the owner of the Kubernetes cluster that is being audited.

To audit your cluster, a small agent is installed in it with one `helm install` command.
It runs as a single pod in its own namespace and can be removed at any time with
`helm uninstall`.

## In short

- The agent is **read-only**. It cannot create, change or delete anything in your cluster.
- It **never reads Secrets or ConfigMaps**. Its Kubernetes role does not include them.
- It only makes **outbound** connections (port 443). No port is opened into your network.
- It can do exactly one thing when asked: list a fixed set of resource types. It cannot
  run commands, open shells or fetch arbitrary data.
- Secret-looking values are **removed inside your cluster**, before anything is sent.

## What is collected

| Area | Resource types | Used for |
| --- | --- | --- |
| Workloads | Pod, Deployment, StatefulSet, DaemonSet, Job, CronJob | security settings, image tags, probes, resource requests and limits |
| Access control | ServiceAccount, Role, ClusterRole, RoleBinding, ClusterRoleBinding | overly broad permissions |
| Network | Namespace, Service, Ingress, NetworkPolicy | exposed services, missing network policies |
| Capacity | Node, ResourceQuota, LimitRange, PodDisruptionBudget | resilience and waste |
| Usage (only if metrics-server is installed) | pod and node metrics | requested versus actually used CPU |

Only the *definitions* of these resources are read: the same information `kubectl get -o yaml`
shows. No application data, no logs, no files from your containers, no network traffic.

## What is never collected

- The contents of **Secrets** and **ConfigMaps**. The agent has no permission to read them.
  If a workload references a secret, only the reference (its name and key) is visible, never
  the value.
- Container logs, files, environment at run time, or anything inside your containers.
- Data from your databases, queues or volumes.

## What is removed before data leaves your cluster

Even though definitions rarely should contain secrets, they sometimes do. The agent therefore
cleans every object before sending it:

- every literal environment variable value is replaced by a placeholder that only keeps its
  length, for example `<redacted:len=12>`;
- values of command-line flags such as `--password=...` or `--token ...` are replaced the same way;
- passwords inside connection URLs are replaced;
- bookkeeping fields that can contain a full copy of a manifest are dropped.

On arrival the data is checked a second time by an independent masking step (known token
formats, private keys, connection strings, high-entropy strings, e-mail addresses). Only this
masked copy is stored and analysed. If an AI model is used to write explanations, it only ever
receives masked excerpts.

## Verify it yourself

Everything above can be checked with standard tools:

```bash
# The agent's complete permission list: only "get" and "list", no secrets, no configmaps
kubectl auth can-i --list --as=system:serviceaccount:ape-system:ape-agent

# These must all answer "no"
kubectl auth can-i get secrets --as=system:serviceaccount:ape-system:ape-agent -A
kubectl auth can-i create pods --as=system:serviceaccount:ape-system:ape-agent -A
kubectl auth can-i delete deployments --as=system:serviceaccount:ape-system:ape-agent -A

# The pod runs as non-root with a read-only file system; a NetworkPolicy blocks all inbound traffic
kubectl -n ape-system get pod,networkpolicy -o wide
```

The agent's source code and its Helm chart are open for review.

## Removing the agent

```bash
helm uninstall ape-agent -n ape-system
kubectl delete namespace ape-system
```

Fixes are never applied to your cluster. They are proposed as a pull request in your Git
repository, which you review and merge yourself.
