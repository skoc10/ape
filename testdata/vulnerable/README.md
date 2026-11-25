# vulnerable test set

Deliberately misconfigured manifests for the audit pipeline. **Never apply these to a real
cluster**: they are meant for the local kind cluster (`task kind-vuln`).

- Every rule of the MVP rule set fires at least once; `expected.yaml` lists the findings the
  rule engine must produce for these two namespaces (`vuln-shop`, `vuln-batch`).
- `clean-app` is the negative control: nothing in it may be reported.
- The manifests carry fake secrets (see `planted-secrets.txt`). None of them may survive
  masking, be stored, or reach the LLM.
- `COST-002` (request far above real usage) needs metrics-server and is therefore covered by
  rule unit tests, not by this set.
