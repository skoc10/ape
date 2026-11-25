"""Applies finding patches to manifest files without disturbing them.

Files are parsed with ruamel.yaml in round-trip mode, so comments, key order and
indentation survive. Multi-document files are supported. Nothing in this module
talks to the network.

A patch is matched to a document in this order (section 4 of mvp-plan.md):

1. ``kind + namespace + name`` exactly;
2. the manifest has no namespace but the ``kustomization.yaml`` next to it sets one;
3. ``kind + name`` alone, if exactly one document in the repository fits.

Anything else is left for a human, with the reason.
"""

import io
import itertools
import posixpath
from dataclasses import dataclass, field
from typing import Any, Literal

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.error import YAMLError

MatchRule = Literal["exact", "kustomize_namespace", "kind_name"]

# Lists whose items are merged by this key instead of being replaced wholesale, as in
# Kubernetes strategic merge patches.
_MERGE_KEYS = ("name", "containerPort", "mountPath", "port")

MAX_FILE_BYTES = 1024 * 1024


def _yaml() -> YAML:
    yaml = YAML(typ="rt")
    yaml.preserve_quotes = True
    yaml.width = 4096  # never re-wrap long lines
    yaml.indent(mapping=2, sequence=4, offset=2)
    return yaml


@dataclass(frozen=True, slots=True)
class Target:
    kind: str
    namespace: str | None
    name: str

    def __str__(self) -> str:
        return f"{self.kind}/{self.namespace or '-'}/{self.name}"


@dataclass(frozen=True, slots=True)
class PatchRequest:
    fingerprint: str
    rule_id: str
    title: str
    target: Target
    patch: str  # YAML, strategic merge


@dataclass(frozen=True, slots=True)
class Applied:
    request: PatchRequest
    path: str
    rule: MatchRule


@dataclass(frozen=True, slots=True)
class Manual:
    request: PatchRequest
    reason: str


@dataclass(slots=True)
class PatchResult:
    files: dict[str, str] = field(default_factory=dict)  # path -> new content, changed files only
    applied: list[Applied] = field(default_factory=list)
    manual: list[Manual] = field(default_factory=list)


@dataclass(slots=True)
class _Document:
    path: str
    index: int
    data: CommentedMap
    kind: str
    name: str
    namespace: str | None  # as written in the manifest
    kustomize_namespace: str | None


@dataclass(slots=True)
class _File:
    path: str
    documents: list[Any]
    indent: tuple[int, int, int]
    changed: bool = False


def _detect_indent(text: str) -> tuple[int, int, int]:
    """(mapping, sequence, offset) of the file, so that re-serialising does not reflow it."""
    mapping = 2
    lines = text.splitlines()
    for previous, line in itertools.pairwise(lines):
        stripped = line.lstrip(" ")
        if not stripped or stripped.startswith("#"):
            continue
        if previous.rstrip().endswith(":") and not previous.lstrip().startswith("-"):
            base = len(previous) - len(previous.lstrip(" "))
            current = len(line) - len(stripped)
            if stripped.startswith("- "):
                offset = current - base
                return mapping, offset + 2, offset
            if current > base:
                mapping = current - base
    return mapping, 4, 2


def _load_file(path: str, text: str) -> _File | None:
    yaml = _yaml()
    try:
        documents = list(yaml.load_all(text))
    except YAMLError:
        return None  # templates and broken files are skipped, never rewritten
    return _File(path, documents, _detect_indent(text))


def _dump_file(file: _File) -> str:
    yaml = _yaml()
    mapping, sequence, offset = file.indent
    yaml.indent(mapping=mapping, sequence=sequence, offset=offset)
    yaml.explicit_start = False
    buffer = io.StringIO()
    yaml.dump_all(file.documents, buffer)
    return buffer.getvalue()


def _merge(target: Any, patch: Any) -> Any:
    """Strategic-merge-like deep merge of ``patch`` into ``target`` (modified in place)."""
    if isinstance(target, CommentedMap) and isinstance(patch, dict):
        for key, value in patch.items():
            if value is None:
                target.pop(key, None)  # null deletes the key, as in a merge patch
            elif key in target:
                target[key] = _merge(target[key], value)
            else:
                target[key] = value
        return target
    if isinstance(target, CommentedSeq) and isinstance(patch, list):
        merge_key = next(
            (
                k
                for k in _MERGE_KEYS
                if patch
                and all(isinstance(i, dict) and isinstance(i.get(k), str | int) for i in patch)
                and all(isinstance(i, dict) and isinstance(i.get(k), str | int) for i in target)
            ),
            None,
        )
        if merge_key is None:
            return patch  # plain lists are replaced
        by_key = {item[merge_key]: item for item in target}
        for item in patch:
            if item[merge_key] in by_key:
                _merge(by_key[item[merge_key]], item)
            else:
                target.append(item)
        return target
    return patch


def _kustomize_namespaces(files: dict[str, _File]) -> dict[str, str]:
    """Directory -> namespace set by the kustomization file in that directory."""
    namespaces: dict[str, str] = {}
    for path, file in files.items():
        if posixpath.basename(path) not in {"kustomization.yaml", "kustomization.yml"}:
            continue
        for document in file.documents:
            if isinstance(document, dict) and isinstance(document.get("namespace"), str):
                namespaces[posixpath.dirname(path)] = document["namespace"]
    return namespaces


def _index(files: dict[str, _File]) -> list[_Document]:
    kustomize = _kustomize_namespaces(files)
    documents: list[_Document] = []
    for path, file in files.items():
        for index, data in enumerate(file.documents):
            if not isinstance(data, CommentedMap):
                continue
            metadata = data.get("metadata")
            kind = data.get("kind")
            if not isinstance(kind, str) or not isinstance(metadata, dict):
                continue
            name = metadata.get("name")
            if not isinstance(name, str):
                continue
            namespace = metadata.get("namespace")
            documents.append(
                _Document(
                    path,
                    index,
                    data,
                    kind,
                    name,
                    namespace if isinstance(namespace, str) else None,
                    kustomize.get(posixpath.dirname(path)),
                )
            )
    return documents


def _match(target: Target, documents: list[_Document]) -> tuple[_Document, MatchRule] | str:
    same = [d for d in documents if d.kind == target.kind and d.name == target.name]
    if not same:
        return "no manifest for this resource in the repository (it may be generated by Helm)"
    if target.namespace is not None:
        exact = [d for d in same if d.namespace == target.namespace]
        if len(exact) == 1:
            return exact[0], "exact"
        if len(exact) > 1:
            return f"{len(exact)} manifests define this resource; not applied automatically"
        via_kustomize = [
            d for d in same if d.namespace is None and d.kustomize_namespace == target.namespace
        ]
        if len(via_kustomize) == 1:
            return via_kustomize[0], "kustomize_namespace"
        if len(via_kustomize) > 1:
            return f"{len(via_kustomize)} manifests define this resource; not applied automatically"
    else:
        cluster_scoped = [d for d in same if d.namespace is None]
        if len(cluster_scoped) == 1 and len(same) == 1:
            return cluster_scoped[0], "exact"
    # Fallback: kind + name only, and only if it cannot be confused with anything else.
    # Manifests that name a different namespace are not candidates.
    candidates = [d for d in same if d.namespace is None and d.kustomize_namespace is None]
    if target.namespace is None:
        candidates = same
    if len(candidates) == 1:
        return candidates[0], "kind_name"
    if len(candidates) > 1:
        return f"ambiguous: {len(candidates)} manifests match by kind and name"
    return "manifests with this kind and name belong to a different namespace"


def apply_patches(sources: dict[str, str], requests: list[PatchRequest]) -> PatchResult:
    """``sources`` maps repository paths to YAML text. Returns the changed files and a
    record of what was applied by which rule and what is left for a human."""
    files: dict[str, _File] = {}
    for path, text in sources.items():
        if len(text.encode()) > MAX_FILE_BYTES:
            continue
        loaded = _load_file(path, text)
        if loaded is not None:
            files[path] = loaded
    documents = _index(files)
    result = PatchResult()
    patch_yaml = YAML(typ="safe")

    for request in requests:
        try:
            patch = patch_yaml.load(request.patch)
        except YAMLError:
            result.manual.append(Manual(request, "the patch is not valid YAML"))
            continue
        if not isinstance(patch, dict):
            result.manual.append(Manual(request, "the patch is not a YAML mapping"))
            continue
        # A patch may restate identity fields; it must never change them.
        if "metadata" in patch and not isinstance(patch["metadata"], dict):
            # ``metadata: null`` would delete the name and namespace, a scalar or a list
            # would replace them.
            result.manual.append(Manual(request, "the patch replaces the resource metadata"))
            continue
        metadata = patch.get("metadata")
        if isinstance(metadata, dict):
            for key in ("name", "namespace"):
                metadata.pop(key, None)
            if not metadata:
                patch.pop("metadata")
        for key in ("kind", "apiVersion"):
            patch.pop(key, None)
        if not patch:
            result.manual.append(Manual(request, "the patch changes nothing"))
            continue

        matched = _match(request.target, documents)
        if isinstance(matched, str):
            result.manual.append(Manual(request, matched))
            continue
        document, rule = matched
        _merge(document.data, patch)
        files[document.path].changed = True
        result.applied.append(Applied(request, document.path, rule))

    for path, file in files.items():
        if file.changed:
            result.files[path] = _dump_file(file)
    return result
