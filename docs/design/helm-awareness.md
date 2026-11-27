# Design: fixes for resources that come from a Helm chart

Status: implemented in `apps/api/ape/github/helm.py` (phase 2, step 1). The plan was
written before the implementation; two points changed while building it and are marked
"as built" below.

## Problem

A finding's patch is a strategic merge patch for the resource as it runs in the cluster.
The pull request feature applies it to the manifest of that resource in the repository.
When the resource is rendered by a Helm chart there is no such manifest: the repository
holds a values file, and the chart decides which value ends up in which field.

In the first real pull request 15 of 20 patches could not be applied for this reason.

Two things have to be found, and neither is in the patch:

1. **Which values file** configures the release the resource belongs to.
2. **Which key** in that file controls the field the patch changes. This is different for
   every chart: `securityContext` in one, `containerSecurityContext` in another,
   `grafana.containerSecurityContext` when the chart is a dependency.

Writing a key the chart does not read is the failure to avoid. Helm ignores unknown
values silently, so such a pull request would look like a fix, merge cleanly, and change
nothing in the cluster.

## Principle

A values file is only changed when there is **evidence for both** the file and the key.
Everything else stays on the manual list, now with a note that names the release and the
chart, so the person knows where to look. Nothing is guessed, and the model is not asked
to guess either: it cannot check a chart it has not seen.

## What the first version does

### 1. Recognise the release (from the cluster snapshot)

From the labels and annotations the audited object already carries:

| Evidence | Gives |
| --- | --- |
| `helm.sh/chart: <name>-<version>` | chart name and version |
| `app.kubernetes.io/instance`, `meta.helm.sh/release-name` | release name |
| `app.kubernetes.io/managed-by: Helm` | that it is a Helm release at all |
| `argocd.argoproj.io/instance`, tracking-id annotation | the Argo CD Application |

Without a chart name the resource is treated as before.

### 2. Find the values file (from the repository)

In this order; the first that gives exactly one file wins, anything ambiguous stops:

1. **Argo CD `Application`** in the repository whose chart and destination namespace
   match: `spec.source.helm.valueFiles`, and the multi-source form
   `$ref/path/to/values.yaml`. Paths are matched from the end, because the values
   repository may be mounted under a different root.
2. **Flux `HelmRelease`** with inline `spec.values` (the HelmRelease file itself is the
   values file) .
3. **Name convention**, only when exactly one file fits: `<release>/values.yaml`,
   `<release>-values.yaml`, `values/<release>.yaml`, and the same with the chart name.

*As built, after the phase 2 review:* three cases gave a file without evidence and now stop.

- An `Application` or `HelmRelease` that deploys the release but does not lead to exactly
  one file in this repository (several value files, inline values, `valuesFrom`, a path in
  another repository) ends the search. Its values are known to be elsewhere, so the name
  convention is not tried.
- Only the `$ref/…` form of `valueFiles` is resolved; a plain path is read from inside the
  chart package. When the path is matched from the end, the file and its directory have to
  agree on both sides: a `values.yaml` at the root of the repository matches nothing.
- A file found by its name that sits next to a `Chart.yaml` is only used when that chart is
  the release's chart itself. A wrapper (umbrella) chart passes values to its dependency
  below the dependency's name or alias; writing them at the top level would be ignored.

Not in the first version: wrapper charts (see above), `ApplicationSet` generators, helmfile, values passed by a
deploy script, values in another repository. These end on the manual list with the note.

### 3. Find the key (from a catalogue)

**Catalogue of well-known charts.** For each chart: the workload it renders (by the
`app.kubernetes.io/component` label and the name of its main container) and, per patchable
field, the values key. Every entry was read off the chart's own workload template, in the
version noted next to it, and is pinned by a test. First charts, chosen from what real
clusters run: cert-manager (controller), argo-cd (redis, dex, notifications, server),
ingress-nginx (controller), opentelemetry-collector, grafana and prometheus-node-exporter,
the last two also as dependencies of kube-prometheus-stack.

A field is left out of an entry when the template does not read it from a plain key: the
container securityContext of ingress-nginx is assembled by a helper, the automount flag of
the node exporter is computed from two values.

No evidence, no change.

*As built:* reading keys from the templates of a chart that lives in the repository was
planned for this version and moved to "Later". Telling the pod's `securityContext` from a
container's needs more than a pattern match on the template, and the first real repository
has no chart of its own to test it against.

### 4. Which patch fields are translated

Only fields whose value does not depend on the chart, and only for the workload's main
container:

| Field in the patch | Typical values key |
| --- | --- |
| container `securityContext.*` | `securityContext` / `containerSecurityContext` |
| pod `securityContext.*` | `podSecurityContext` |
| pod `automountServiceAccountToken` | `automountServiceAccountToken` / `serviceAccount.automount` |
| container `resources` | `resources` |

A patch that touches anything else (probes, env, volumes, a sidecar, several containers)
is not translated at all, not even partly: half a fix is harder to review than none.

### 5. Output

- **Pull request:** the values file is edited in place with the same round-trip writer as
  manifests, so comments and order survive. The table of changes says
  "Helm values: chart, key".
- **Download fixes:** a `helm-values/<release>.yaml` overlay per release for every
  translated patch, also when no values file was found in a repository. It can be passed
  to `helm upgrade -f` or copied into the real values file.
- **Manual list:** untranslated Helm patches carry the release, chart and version.

## Safety limits

- Templated files (`{{`, `{%`) are never rewritten; this already holds for manifests.
- A values file is only touched when it was identified by step 2. No file is created in
  the repository.
- A key that already holds a different type (a string where a mapping is expected) stops
  the translation: the file is then not what we think it is. *As built:* an existing scalar
  is replaced by the patch's value, because replacing it is the fix; the pull request diff
  shows the old value.
- Masked placeholders never reach a values file, as with manifests.
- Chart version: catalogue entries state the version they were checked against. A release
  on another major version is still translated, and the pull request says so. This is the
  one place where the evidence is weaker; it is stated rather than hidden.
- No chart is downloaded and no `helm` binary is run by the Control Plane. Fetching charts
  named in a customer's repository would let that repository make the server call
  arbitrary addresses.

## Later, not now

- **Keys from a chart in the repository:** a template line such as
  `securityContext: {{- toYaml .Values.securityContext | nindent 12 }}` is proof of the key
  and would cover in-house charts made from the `helm create` scaffold.
- **Verified mode:** render the chart before and after (`helm template`) in an isolated
  job and keep only changes that show up in the rendered resource. This would replace the
  catalogue as evidence and allow probes and other chart-specific fields. It needs a
  sandbox with controlled network access.
- `ApplicationSet`, helmfile, values in a second repository.
- Kustomize `helmCharts` and `patches` as a target.
