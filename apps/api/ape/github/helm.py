"""Fixes for resources that are rendered by a Helm chart.

A finding's patch describes the resource as it runs in the cluster. For a Helm release the
repository holds a values file instead of that resource, and the chart decides which value
ends up in which field. This module turns a patch into a change of the values file, but
only when there is evidence for both the file and the key (docs/design/helm-awareness.md).

Helm ignores values it does not know. A key that is merely plausible would give a pull
request that looks like a fix, merges cleanly and changes nothing, so nothing is guessed.

Nothing here talks to the network, downloads a chart or runs ``helm``.
"""

import posixpath
import re
from dataclasses import dataclass
from typing import Any

import yaml

Path = tuple[str, ...]

# Where the pod spec lives in a workload, by kind.
_POD_SPEC_PATH: dict[str, Path] = {
    "Deployment": ("spec", "template", "spec"),
    "StatefulSet": ("spec", "template", "spec"),
    "DaemonSet": ("spec", "template", "spec"),
}

_VALUES_SUFFIXES = (".yaml", ".yml", ".yaml.tpl", ".yml.tpl")


@dataclass(frozen=True, slots=True)
class Release:
    """What a cluster object says about the Helm release it belongs to."""

    chart: str
    version: str
    instance: str
    namespace: str | None
    component: str | None

    def __str__(self) -> str:
        return f"Helm release {self.instance} (chart {self.chart} {self.version})"


@dataclass(frozen=True, slots=True)
class Keys:
    """Where one workload of a chart reads the fields we can translate.

    Every path was read off the chart's own template, in the version given by ``checked``;
    a field without a verified key is None and is not translated.
    """

    main_container: str
    checked: str
    component: str | None = None  # value of app.kubernetes.io/component; None: any
    container_security_context: Path | None = None
    pod_security_context: Path | None = None
    resources: Path | None = None
    automount: Path | None = None  # the pod's automountServiceAccountToken


# chart name -> workloads. Sources: the charts' deployment/daemonset templates.
CATALOGUE: dict[str, tuple[Keys, ...]] = {
    "opentelemetry-collector": (
        Keys(
            main_container="opentelemetry-collector",
            checked="0.183.2",
            container_security_context=("securityContext",),
            pod_security_context=("podSecurityContext",),
            resources=("resources",),
            automount=("serviceAccount", "automountServiceAccountToken"),
        ),
    ),
    "prometheus-node-exporter": (
        Keys(
            main_container="node-exporter",
            checked="6.0.0",
            container_security_context=("containerSecurityContext",),
            pod_security_context=("securityContext",),
            resources=("resources",),
            # The pod's flag is computed from two values there: not a plain key.
        ),
    ),
    "grafana": (
        Keys(
            main_container="grafana",
            checked="14.6.1",
            container_security_context=("containerSecurityContext",),
            pod_security_context=("securityContext",),
            resources=("resources",),
            automount=("automountServiceAccountToken",),
        ),
    ),
    "cert-manager": (
        Keys(
            main_container="cert-manager-controller",
            checked="1.18.2",
            component="controller",
            container_security_context=("containerSecurityContext",),
            pod_security_context=("securityContext",),
            resources=("resources",),
            automount=("automountServiceAccountToken",),
        ),
    ),
    "ingress-nginx": (
        Keys(
            main_container="controller",
            checked="4.15.1",
            component="controller",
            # The container's securityContext is assembled by a helper: not a plain key.
            pod_security_context=("controller", "podSecurityContext"),
            resources=("controller", "resources"),
            automount=("serviceAccount", "automountServiceAccountToken"),
        ),
    ),
    "argo-cd": (
        Keys(
            main_container="redis",
            checked="10.20.0",
            component="redis",
            container_security_context=("redis", "containerSecurityContext"),
            pod_security_context=("redis", "securityContext"),
            resources=("redis", "resources"),
            automount=("redis", "automountServiceAccountToken"),
        ),
        Keys(
            main_container="dex-server",
            checked="10.20.0",
            component="dex-server",
            container_security_context=("dex", "containerSecurityContext"),
            resources=("dex", "resources"),
            automount=("dex", "automountServiceAccountToken"),
        ),
        Keys(
            main_container="notifications-controller",
            checked="10.20.0",
            component="notifications-controller",
            container_security_context=("notifications", "containerSecurityContext"),
            resources=("notifications", "resources"),
            automount=("notifications", "automountServiceAccountToken"),
        ),
        Keys(
            main_container="server",
            checked="10.20.0",
            component="server",
            automount=("server", "automountServiceAccountToken"),
        ),
    ),
}

# Charts that ship other charts: parent -> {dependency chart: key its values live under}.
PARENTS: dict[str, dict[str, str]] = {
    "kube-prometheus-stack": {
        "grafana": "grafana",
        "prometheus-node-exporter": "prometheus-node-exporter",
    },
}

_CHART_LABEL = re.compile(r"^(?P<name>.+)-(?P<version>v?\d+\.\d+\.\d+.*)$")


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def release_of(obj: dict[str, Any] | None) -> Release | None:
    """The release an audited object belongs to, from the labels Helm charts set."""
    metadata = _mapping(_mapping(obj).get("metadata"))
    labels = _mapping(metadata.get("labels"))
    match = _CHART_LABEL.match(str(labels.get("helm.sh/chart") or ""))
    instance = labels.get("app.kubernetes.io/instance")
    if not match or not isinstance(instance, str) or not instance:
        return None
    component = labels.get("app.kubernetes.io/component")
    namespace = metadata.get("namespace")
    return Release(
        chart=match["name"],
        version=match["version"],
        instance=instance,
        namespace=namespace if isinstance(namespace, str) else None,
        component=component if isinstance(component, str) else None,
    )


def keys_for(release: Release, obj: dict[str, Any]) -> Keys | None:
    """The catalogue entry for this workload, if the chart is known and the workload has
    the container the entry was written for."""
    kind = str(obj.get("kind") or "")
    pod_spec = obj
    for key in _POD_SPEC_PATH.get(kind, ("?",)):
        pod_spec = _mapping(pod_spec.get(key))
    names = [c.get("name") for c in pod_spec.get("containers") or [] if isinstance(c, dict)]
    for keys in CATALOGUE.get(release.chart, ()):
        if keys.component is not None and keys.component != release.component:
            continue
        if keys.main_container in names:
            return keys
    return None


_IDENTITY_KEYS = {"apiVersion", "kind", "metadata"}


def translate(patch: Any, kind: str, keys: Keys) -> dict[Path, Any] | None:
    """A strategic merge patch as values to set, or None when any part of it has no
    verified key. Never partial: half a fix is harder to review than none."""
    path = _POD_SPEC_PATH.get(kind)
    if path is None or not isinstance(patch, dict):
        return None
    if set(patch) - _IDENTITY_KEYS - {path[0]}:
        return None
    node: Any = patch
    for index, key in enumerate(path):
        node = node.get(key) if isinstance(node, dict) else None
        if not isinstance(node, dict):
            return None
        # Nothing but the way down to the pod spec (and labels nobody patches here).
        if index < len(path) - 1 and set(node) - {path[index + 1]}:
            return None
    pod_spec: dict[str, Any] = node

    values: dict[Path, Any] = {}
    for field, value in pod_spec.items():
        if field == "automountServiceAccountToken" and keys.automount and isinstance(value, bool):
            values[keys.automount] = value
        elif field == "securityContext" and keys.pod_security_context and _plain(value):
            values[keys.pod_security_context] = value
        elif field == "containers":
            container = _only_main_container(value, keys.main_container)
            if container is None:
                return None
            for name, part in container.items():
                if name == "name":
                    continue
                if name == "securityContext" and keys.container_security_context and _plain(part):
                    values[keys.container_security_context] = part
                elif name == "resources" and keys.resources and _plain(part):
                    values[keys.resources] = part
                else:
                    return None
        else:
            return None
    return values or None


def _plain(value: Any) -> bool:
    """A non-empty mapping of scalars and nested mappings or lists of scalars."""
    return isinstance(value, dict) and bool(value)


def _only_main_container(value: Any, main: str) -> dict[str, Any] | None:
    if not isinstance(value, list) or len(value) != 1 or not isinstance(value[0], dict):
        return None  # a sidecar or several containers: other keys, not known here
    return value[0] if value[0].get("name") == main else None


def nest(values: dict[Path, Any], prefix: Path = ()) -> dict[str, Any]:
    """``{("a", "b"): 1}`` as ``{"a": {"b": 1}}``, below ``prefix``."""
    root: dict[str, Any] = {}
    for path, value in values.items():
        node = root
        full = (*prefix, *path)
        for key in full[:-1]:
            node = node.setdefault(key, {})
        node[full[-1]] = value
    return root


def dotted(path: Path) -> str:
    return ".".join(path)


# --- finding the values file -----------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ValuesTarget:
    path: str
    document: int  # index of the YAML document in the file
    prefix: Path  # where the values start inside that document
    parent: str | None  # the chart the file configures, when the release's chart is a dependency
    found_by: str


def _documents(sources: dict[str, str]) -> dict[str, list[Any]]:
    parsed: dict[str, list[Any]] = {}
    for path, text in sources.items():
        if "{{" in text or "{%" in text:
            continue
        try:
            parsed[path] = list(yaml.safe_load_all(text))
        except yaml.YAMLError:
            continue
    return parsed


def _charts_of(release: Release) -> dict[str, str | None]:
    """Chart names a values file for this release may be written for -> parent or None."""
    charts: dict[str, str | None] = {release.chart: None}
    for parent, dependencies in PARENTS.items():
        if release.chart in dependencies:
            charts[parent] = parent
    return charts


def _resolve(value_file: str, paths: list[str]) -> str | None:
    """A valueFiles entry as a path of this repository. Argo CD's ``$ref/…`` form points
    into another source, which may be this repository under a different root, so paths
    are compared from the end and at least the file and its directory have to agree."""
    wanted = value_file.split("/", 1)[1] if value_file.startswith("$") else value_file
    wanted = wanted.lstrip("./")
    if wanted.count("/") < 1:
        return None
    matches = [
        p for p in paths if p == wanted or p.endswith("/" + wanted) or wanted.endswith("/" + p)
    ]
    return matches[0] if len(matches) == 1 else None


def _from_argo_cd(
    release: Release, documents: dict[str, list[Any]], paths: list[str]
) -> list[ValuesTarget]:
    charts = _charts_of(release)
    found: list[ValuesTarget] = []
    for docs in documents.values():
        for doc in docs:
            if not isinstance(doc, dict) or doc.get("kind") != "Application":
                continue
            if not str(doc.get("apiVersion", "")).startswith("argoproj.io/"):
                continue
            spec = _mapping(doc.get("spec"))
            namespace = _mapping(spec.get("destination")).get("namespace")
            if release.namespace and namespace and namespace != release.namespace:
                continue
            listed = spec.get("sources")
            sources: list[Any] = listed if isinstance(listed, list) else []
            for source in [spec.get("source"), *sources]:
                source = _mapping(source)
                if source.get("chart") not in charts:
                    continue
                helm = _mapping(source.get("helm"))
                name = helm.get("releaseName") or _mapping(doc.get("metadata")).get("name")
                if name != release.instance:
                    continue
                files = helm.get("valueFiles")
                files = files if isinstance(files, list) else []
                # With several files the last one wins in Helm, and which one is meant
                # to carry this setting is the owner's decision: not ours to pick.
                if len(files) != 1 or not isinstance(files[0], str):
                    continue
                path = _resolve(files[0], paths)
                if path is not None:
                    found.append(
                        ValuesTarget(
                            path, 0, (), charts[str(source["chart"])], "Argo CD Application"
                        )
                    )
    return found


def _from_flux(release: Release, documents: dict[str, list[Any]]) -> list[ValuesTarget]:
    charts = _charts_of(release)
    found: list[ValuesTarget] = []
    for path, docs in documents.items():
        for index, doc in enumerate(docs):
            if not isinstance(doc, dict) or doc.get("kind") != "HelmRelease":
                continue
            if not str(doc.get("apiVersion", "")).startswith("helm.toolkit.fluxcd.io/"):
                continue
            metadata, spec = _mapping(doc.get("metadata")), _mapping(doc.get("spec"))
            chart = _mapping(_mapping(spec.get("chart")).get("spec")).get("chart")
            if chart not in charts:
                continue
            if (spec.get("releaseName") or metadata.get("name")) != release.instance:
                continue
            target = spec.get("targetNamespace") or metadata.get("namespace")
            if release.namespace and target and target != release.namespace:
                continue
            # Only inline values: valuesFrom points at ConfigMaps and Secrets.
            if isinstance(spec.get("values"), dict) and not spec.get("valuesFrom"):
                found.append(
                    ValuesTarget(
                        path, index, ("spec", "values"), charts[str(chart)], "Flux HelmRelease"
                    )
                )
    return found


def _looks_like_values(docs: list[Any]) -> bool:
    documents = [d for d in docs if d is not None]
    if len(documents) != 1 or not isinstance(documents[0], dict):
        return False
    return not ("apiVersion" in documents[0] and "kind" in documents[0])


def _matches_name(path: str, name: str) -> bool:
    """``<name>/values.yaml``, ``<name>-values.yaml``, ``values-<name>.yaml``,
    ``values/<name>.yaml`` (and the ``.tpl`` forms)."""
    directory, base = posixpath.split(path)
    stem = next((base[: -len(s)] for s in _VALUES_SUFFIXES if base.endswith(s)), base)
    folder = posixpath.basename(directory)
    return (
        (stem == "values" and folder == name)
        or stem in {f"{name}-values", f"values-{name}"}
        or (stem == name and folder == "values")
    )


def _from_names(release: Release, documents: dict[str, list[Any]]) -> list[ValuesTarget]:
    parents = [p for p, dependencies in PARENTS.items() if release.chart in dependencies]
    found: list[ValuesTarget] = []
    for path, docs in documents.items():
        if not _looks_like_values(docs):
            continue
        content = next(d for d in docs if d is not None)
        parent: str | None
        named_after = [p for p in parents if _matches_name(path, p)]
        if named_after:
            parent = named_after[0]
        elif _matches_name(path, release.chart):
            parent = None
        elif _matches_name(path, release.instance):
            # The release name says nothing about the chart. For a chart that is usually
            # a dependency, the file has to show which of the two it configures.
            inside = [p for p in parents if PARENTS[p][release.chart] in content]
            if len(inside) == 1:
                parent = inside[0]
            elif parents:
                continue
            else:
                parent = None
        else:
            continue
        found.append(ValuesTarget(path, 0, (), parent, "file name"))
    return found


def find_values(release: Release, sources: dict[str, str]) -> ValuesTarget | str:
    """The one place in the repository that holds this release's values, or the reason
    there is none. Ambiguity is a reason too."""
    documents = _documents(sources)
    paths = sorted(sources)
    candidates = (
        _from_argo_cd(release, documents, paths),
        _from_flux(release, documents),
        _from_names(release, documents),
    )
    for found in candidates:
        targets = list(dict.fromkeys(found))
        if len(targets) == 1:
            target = targets[0]
            if target.path not in documents or not _usable(documents[target.path], target):
                return "its values file cannot be edited safely"
            return target
        if len(targets) > 1:
            return "several values files could belong to it"
    return "no values file for it was found in the repository"


def _usable(docs: list[Any], target: ValuesTarget) -> bool:
    if target.document >= len(docs) or not isinstance(docs[target.document], dict):
        return False
    return target.prefix != () or _looks_like_values(docs)


def conflict(existing: Any, values: dict[str, Any]) -> str | None:
    """Why ``values`` cannot be merged into the file's current content, if it cannot: a key
    that holds something of another shape means the file is not what we think it is."""
    for key, value in values.items():
        if not isinstance(existing, dict) or key not in existing or existing[key] is None:
            continue
        current = existing[key]
        if isinstance(value, dict):
            if not isinstance(current, dict):
                return f"the key {key} already holds a value of another type"
            problem = conflict(current, value)
            if problem:
                return problem
        elif isinstance(current, dict | list):
            return f"the key {key} already holds a value of another type"
    return None


def version_note(release: Release, keys: Keys) -> str:
    """Says so when the key was checked against another major version of the chart."""

    def major(version: str) -> str:
        parts = version.lstrip("v").split(".")
        return ".".join(parts[:2]) if parts[0] == "0" else parts[0]

    if major(release.version) == major(keys.checked):
        return ""
    return f"; key checked against chart version {keys.checked}"


# --- one patch, start to finish ----------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Translation:
    release: Release
    keys: Keys
    values: dict[Path, Any]

    def overlay(self, parent: str | None, prefix: Path = ()) -> dict[str, Any]:
        """The values as they have to appear in a file for ``parent`` (or the chart itself)."""
        below = (PARENTS[parent][self.release.chart],) if parent else ()
        return nest(self.values, (*prefix, *below))

    def describe(self, parent: str | None) -> str:
        below = (PARENTS[parent][self.release.chart],) if parent else ()
        keys = ", ".join(dotted((*below, *path)) for path in sorted(self.values))
        chart = f"{parent} with {self.release.chart}" if parent else self.release.chart
        return (
            f"chart {chart} {self.release.version}, {keys}{version_note(self.release, self.keys)}"
        )


def translation_for(obj: dict[str, Any] | None, patch_text: str) -> Translation | str | None:
    """None when the object is not part of a Helm release; a reason when it is but the
    patch cannot be expressed as values; otherwise the values."""
    release = release_of(obj)
    if release is None or obj is None:
        return None
    keys = keys_for(release, obj)
    if keys is None:
        return f"rendered by {release}; no verified value keys are known for this workload"
    try:
        patch = yaml.safe_load(patch_text)
    except yaml.YAMLError:
        return f"rendered by {release}; the patch is not valid YAML"
    values = translate(patch, str(obj.get("kind") or ""), keys)
    if values is None:
        return (
            f"rendered by {release}; the patch changes fields that have no verified value "
            "key (only the main container's securityContext and resources, the pod's "
            "securityContext and automountServiceAccountToken are translated)"
        )
    return Translation(release, keys, values)


def standalone_parent(release: Release) -> tuple[bool, str | None]:
    """Without a values file to look at: ``(known, parent)``. The parent is the chart the
    release is named after when this chart is one of its dependencies, None when the chart
    stands alone; ``known`` is False when it cannot be told which of the two it is."""
    parents = [p for p, dependencies in PARENTS.items() if release.chart in dependencies]
    if release.instance in parents:
        return True, release.instance
    if not parents or release.instance == release.chart:
        return True, None
    return False, None
