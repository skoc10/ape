"""Manifest patching with ruamel.yaml (task 4.4): comments survive, matching rules hold."""

import pytest

from ape.github.patcher import PatchRequest, Target, apply_patches

DEPLOYMENT = """\
# Storefront deployment. Owned by team payments.
apiVersion: apps/v1
kind: Deployment
metadata:
  name: storefront
  namespace: shop   # production namespace
  labels:
    app: storefront
spec:
  replicas: 1  # TODO: scale up before Black Friday
  selector:
    matchLabels:
      app: storefront
  template:
    metadata:
      labels:
        app: storefront
    spec:
      hostNetwork: true   # legacy, see OPS-123
      containers:
        # the main container
        - name: web
          image: "ghcr.io/acme/storefront:1.4.2"
          env:
            - name: LOG_LEVEL
              value: 'debug'
          securityContext:
            privileged: true
        - name: sidecar
          image: ghcr.io/acme/proxy:2.0.0
"""

PATCH = """\
spec:
  template:
    spec:
      hostNetwork: false
      containers:
        - name: web
          securityContext:
            privileged: false
            allowPrivilegeEscalation: false
"""


def request(
    kind: str = "Deployment",
    namespace: str | None = "shop",
    name: str = "storefront",
    patch: str = PATCH,
) -> PatchRequest:
    return PatchRequest(
        "f" * 64, "SEC-001", "Privileged container", Target(kind, namespace, name), patch
    )


def test_exact_match_patches_only_the_named_fields_and_keeps_comments() -> None:
    result = apply_patches({"k8s/storefront.yaml": DEPLOYMENT}, [request()])
    assert [(a.path, a.rule) for a in result.applied] == [("k8s/storefront.yaml", "exact")]
    assert result.manual == []
    patched = result.files["k8s/storefront.yaml"]

    expected = DEPLOYMENT.replace("hostNetwork: true ", "hostNetwork: false").replace(
        "            privileged: true\n",
        "            privileged: false\n            allowPrivilegeEscalation: false\n",
    )
    assert patched == expected  # byte for byte: comments, quotes, order and indentation intact
    for comment in (
        "# Storefront deployment",
        "# production namespace",
        "# TODO: scale up",
        "# legacy, see OPS-123",
        "# the main container",
    ):
        assert comment in patched
    assert 'image: "ghcr.io/acme/storefront:1.4.2"' in patched and "value: 'debug'" in patched
    assert "name: sidecar" in patched  # the other container is untouched, not replaced


def test_manifest_without_namespace_falls_back_to_kind_and_name() -> None:
    manifest = DEPLOYMENT.replace("  namespace: shop   # production namespace\n", "")
    result = apply_patches({"deploy.yaml": manifest}, [request()])
    assert [(a.path, a.rule) for a in result.applied] == [("deploy.yaml", "kind_name")]
    assert "hostNetwork: false" in result.files["deploy.yaml"]


def test_multiple_candidates_are_ambiguous_and_left_for_a_human() -> None:
    manifest = DEPLOYMENT.replace("  namespace: shop   # production namespace\n", "")
    sources = {"overlays/eu/deploy.yaml": manifest, "overlays/us/deploy.yaml": manifest}
    result = apply_patches(sources, [request()])
    assert result.applied == [] and result.files == {}
    assert len(result.manual) == 1
    assert "ambiguous: 2 manifests" in result.manual[0].reason


def test_kustomization_namespace_resolves_the_right_overlay() -> None:
    manifest = DEPLOYMENT.replace("  namespace: shop   # production namespace\n", "")
    sources = {
        "overlays/shop/deploy.yaml": manifest,
        "overlays/shop/kustomization.yaml": "namespace: shop\nresources:\n  - deploy.yaml\n",
        "overlays/staging/deploy.yaml": manifest,
        "overlays/staging/kustomization.yaml": "namespace: staging\nresources:\n  - deploy.yaml\n",
    }
    result = apply_patches(sources, [request()])
    assert [(a.path, a.rule) for a in result.applied] == [
        ("overlays/shop/deploy.yaml", "kustomize_namespace")
    ]
    assert list(result.files) == ["overlays/shop/deploy.yaml"]


def test_a_manifest_of_another_namespace_is_never_patched() -> None:
    other = DEPLOYMENT.replace("namespace: shop ", "namespace: staging ")
    result = apply_patches({"staging.yaml": other}, [request()])
    assert result.applied == [] and result.files == {}
    assert "different namespace" in result.manual[0].reason


def test_multi_document_files_and_cluster_scoped_resources() -> None:
    multi = (
        "# RBAC for the batch jobs\n"
        "apiVersion: rbac.authorization.k8s.io/v1\n"
        "kind: ClusterRole\n"
        "metadata:\n"
        "  name: batch\n"
        "rules:\n"
        '  - apiGroups: ["*"]   # far too broad\n'
        '    resources: ["*"]\n'
        '    verbs: ["*"]\n'
        "---\n" + DEPLOYMENT
    )
    role_patch = (
        'rules:\n  - apiGroups: [""]\n    resources: ["pods"]\n    verbs: ["get", "list"]\n'
    )
    result = apply_patches(
        {"all.yaml": multi}, [request("ClusterRole", None, "batch", role_patch), request()]
    )
    assert {(a.request.target.kind, a.rule) for a in result.applied} == {
        ("ClusterRole", "exact"),
        ("Deployment", "exact"),
    }
    patched = result.files["all.yaml"]
    assert patched.count("\n---\n") == 1
    assert "# RBAC for the batch jobs" in patched and "# Storefront deployment" in patched
    assert (
        "resources:" in patched
        and "pods" in patched
        and '["*"]' not in patched.split("---")[0].split("rules:")[1]
    )
    assert "hostNetwork: false" in patched


def test_unmatched_and_invalid_patches_go_to_the_manual_list() -> None:
    sources = {
        "k8s/storefront.yaml": DEPLOYMENT,
        "chart/templates/deploy.yaml": "{{- if .Values.enabled }}\nkind: x\n{{- end }}\n",
    }
    requests = [
        request(name="from-a-helm-chart"),
        request(patch="spec: [unclosed"),
        request(patch="just text"),
        request(patch="metadata:\n  name: storefront\n  namespace: shop\n"),
    ]
    result = apply_patches(sources, requests)
    assert result.applied == [] and result.files == {}
    reasons = [m.reason for m in result.manual]
    assert "no manifest for this resource" in reasons[0]
    assert reasons[1:] == [
        "the patch is not valid YAML",
        "the patch is not a YAML mapping",
        "the patch changes nothing",
    ]


def test_a_patch_cannot_rename_or_move_a_resource() -> None:
    hostile = 'kind: ClusterRoleBinding\nmetadata:\n  name: pwned\n  namespace: kube-system\n  labels:\n    patched: "yes"\n'
    result = apply_patches({"k8s/storefront.yaml": DEPLOYMENT}, [request(patch=hostile)])
    patched = result.files["k8s/storefront.yaml"]
    assert (
        "name: storefront" in patched
        and "namespace: shop" in patched
        and "kind: Deployment" in patched
    )
    assert "pwned" not in patched and "kube-system" not in patched
    assert 'patched: "yes"' in patched or "patched: 'yes'" in patched or "patched: yes" in patched


def test_two_patches_for_the_same_file_are_both_applied() -> None:
    probes = "spec:\n  template:\n    spec:\n      containers:\n        - name: sidecar\n          readinessProbe:\n            tcpSocket:\n              port: 8080\n"
    result = apply_patches({"k8s/storefront.yaml": DEPLOYMENT}, [request(), request(patch=probes)])
    patched = result.files["k8s/storefront.yaml"]
    assert len(result.applied) == 2
    assert (
        "hostNetwork: false" in patched
        and "readinessProbe:" in patched
        and "# the main container" in patched
    )


def test_untouched_files_are_not_rewritten() -> None:
    other = "apiVersion: v1\nkind: Service\nmetadata: {name: web,   namespace: shop}  # odd spacing stays\n"
    result = apply_patches(
        {"k8s/storefront.yaml": DEPLOYMENT, "k8s/service.yaml": other}, [request()]
    )
    assert list(result.files) == ["k8s/storefront.yaml"]


@pytest.mark.parametrize("metadata", ["null", "gone", "[a, b]", "~"])
def test_a_patch_cannot_delete_or_replace_the_metadata(metadata: str) -> None:
    hostile = f"metadata: {metadata}\nspec:\n  replicas: 0\n"
    result = apply_patches({"k8s/storefront.yaml": DEPLOYMENT}, [request(patch=hostile)])
    assert result.files == {} and result.applied == []
    assert [m.reason for m in result.manual] == ["the patch replaces the resource metadata"]


def test_list_items_with_unusable_merge_keys_replace_the_list_instead_of_crashing() -> None:
    odd = "spec:\n  template:\n    spec:\n      containers:\n        - name: [not, a, name]\n          image: x\n"
    result = apply_patches({"k8s/storefront.yaml": DEPLOYMENT}, [request(patch=odd)])
    assert len(result.applied) == 1 and "k8s/storefront.yaml" in result.files


def test_envsubst_templates_are_patched_and_keep_their_placeholders() -> None:
    template = DEPLOYMENT.replace(
        "ghcr.io/acme/storefront:1.4.2", "registry.example/app@${IMAGE_DIGEST}"
    )
    assert "${IMAGE_DIGEST}" in template
    result = apply_patches({"k8s/storefront.yaml.tpl": template}, [request()])
    assert [(a.path, a.rule) for a in result.applied] == [("k8s/storefront.yaml.tpl", "exact")]
    patched = result.files["k8s/storefront.yaml.tpl"]
    assert "registry.example/app@${IMAGE_DIGEST}" in patched
    assert "allowPrivilegeEscalation: false" in patched


@pytest.mark.parametrize(
    "marker",
    [
        "  replicas: {{ .Values.replicas }}",
        "  # {% if production %}",
        '    note: "{{ $labels.pod }}"',
    ],
)
def test_go_helm_and_jinja_templates_are_never_rewritten(marker: str) -> None:
    """Some of them parse as YAML and would come back mangled, so they are left alone."""
    templated = DEPLOYMENT.replace("spec:\n", f"spec:\n{marker}\n", 1)
    result = apply_patches({"chart/templates/storefront.yaml": templated}, [request()])
    assert result.files == {} and result.applied == []
    assert [m.reason for m in result.manual] == [
        "no manifest for this resource in the repository (it may be generated by Helm)"
    ]
