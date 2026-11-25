"""Read-only view over the collected (and already masked) objects."""

import re
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from ape.rules.model import ResourceRef

# Namespaces managed by Kubernetes itself; findings there are not the customer's to fix.
SYSTEM_NAMESPACES = frozenset({"kube-system", "kube-public", "kube-node-lease"})

Obj = dict[str, Any]


def meta(obj: Obj) -> Obj:
    value = obj.get("metadata")
    return value if isinstance(value, dict) else {}


def ref(obj: Obj) -> ResourceRef:
    m = meta(obj)
    return ResourceRef(str(obj.get("kind", "")), m.get("namespace"), str(m.get("name", "")))


def as_dict(value: Any) -> Obj:
    return value if isinstance(value, dict) else {}


def as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def matches(selector: Any, labels: Any) -> bool:
    """matchLabels-only selector semantics; an empty selector matches nothing here."""
    wanted = as_dict(as_dict(selector).get("matchLabels"))
    have = as_dict(labels)
    return bool(wanted) and all(have.get(k) == v for k, v in wanted.items())


@dataclass(frozen=True, slots=True)
class Workload:
    """Anything that runs pods, reduced to what the rules look at."""

    obj: Obj
    ref: ResourceRef
    pod_spec: Obj
    pod_labels: Obj
    long_running: bool  # false for Job and CronJob

    @property
    def containers(self) -> list[Obj]:
        return [c for c in as_list(self.pod_spec.get("containers")) if isinstance(c, dict)]

    @property
    def all_containers(self) -> list[Obj]:
        init = [c for c in as_list(self.pod_spec.get("initContainers")) if isinstance(c, dict)]
        return [*self.containers, *init]


def _workload(obj: Obj) -> Workload | None:
    kind = obj.get("kind")
    spec = as_dict(obj.get("spec"))
    owners = as_list(meta(obj).get("ownerReferences"))
    if kind == "Pod":
        if owners:
            return None  # reported once, on the controller that owns it
        return Workload(obj, ref(obj), spec, as_dict(meta(obj).get("labels")), True)
    if kind in {"Deployment", "StatefulSet", "DaemonSet"}:
        template = as_dict(spec.get("template"))
        labels = as_dict(as_dict(template.get("metadata")).get("labels"))
        return Workload(obj, ref(obj), as_dict(template.get("spec")), labels, True)
    if kind == "Job":
        if any(as_dict(o).get("kind") == "CronJob" for o in owners):
            return None
        template = as_dict(spec.get("template"))
        labels = as_dict(as_dict(template.get("metadata")).get("labels"))
        return Workload(obj, ref(obj), as_dict(template.get("spec")), labels, False)
    if kind == "CronJob":
        template = as_dict(as_dict(as_dict(spec.get("jobTemplate")).get("spec")).get("template"))
        labels = as_dict(as_dict(template.get("metadata")).get("labels"))
        return Workload(obj, ref(obj), as_dict(template.get("spec")), labels, False)
    return None


class Snapshot:
    def __init__(self, objects: list[Obj], *, include_system_namespaces: bool = False) -> None:
        self._by_kind: dict[str, list[Obj]] = defaultdict(list)
        for obj in objects:
            if not isinstance(obj, dict) or not isinstance(obj.get("kind"), str):
                continue
            namespace = meta(obj).get("namespace")
            name = meta(obj).get("name")
            if not include_system_namespaces and (
                namespace in SYSTEM_NAMESPACES
                or (obj["kind"] == "Namespace" and name in SYSTEM_NAMESPACES)
            ):
                continue
            self._by_kind[obj["kind"]].append(obj)
        self.workloads: list[Workload] = [
            w
            for kind in ("Deployment", "StatefulSet", "DaemonSet", "Job", "CronJob", "Pod")
            for obj in self._by_kind[kind]
            if (w := _workload(obj)) is not None
        ]

    def kind(self, kind: str) -> list[Obj]:
        return self._by_kind.get(kind, [])


_CPU = re.compile(r"^(\d+(?:\.\d+)?)(n|u|m)?$")


def cpu_millicores(quantity: Any) -> float | None:
    """Parses a Kubernetes CPU quantity ("250m", "0.5", "1", "12345678n") to millicores."""
    if isinstance(quantity, int | float):
        return float(quantity) * 1000
    if not isinstance(quantity, str):
        return None
    match = _CPU.match(quantity.strip())
    if not match:
        return None
    value = float(match.group(1))
    factor = {None: 1000.0, "m": 1.0, "u": 0.001, "n": 0.000001}[match.group(2)]
    return value * factor
