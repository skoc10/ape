# Security checklist

Every item below is enforced by an automated test that runs in CI. A release is only
acceptable when all of them are green and the manual item at the end is confirmed.

| # | Guarantee | Where it is proven | CI workflow |
| --- | --- | --- | --- |
| 1 | A tenant can never read or write another tenant's rows (RLS with `FORCE`, composite foreign keys, three database roles) | `db/tests/tenant_isolation.sql` (pgTAP) | `db` |
| 2 | The API pins every transaction to the caller's organization; SQL lives only in `ape/repos` | `apps/api/tests/test_auth.py`, `test_architecture.py`, cross-tenant cases in `test_clusters.py`, `test_audits.py`, `test_findings_api.py`, `test_settings.py`, `test_github.py` | `api` |
| 3 | Tokens are verified against JWKS with a pinned algorithm; membership is re-checked on every request | `apps/api/tests/test_auth.py` | `api` |
| 4 | The agent is read-only and cannot reach Secrets or ConfigMaps | `deploy/helm/ape-agent/ci/rbac-check.sh` on a kind cluster; `agent/internal/collect/collector_test.go` (only `list` calls) | `charts`, `agent` |
| 5 | Secrets are removed at the source (agent) | `agent/internal/collect/sanitize_test.go` | `agent` |
| 6 | Secrets are masked again on arrival; only the masked copy is stored | `apps/api/tests/sanitize/` (corpus of 60+ samples), `test_audits.py`, end-to-end run | `api`, `e2e` |
| 7 | No secret reaches the LLM: the model call accepts only a `MaskedPayload`, and the payload is masked once more on the way out | `apps/api/tests/test_analyst.py` (`test_nothing_unmasked_reaches_the_model`), `test_security_checklist.py` | `api` |
| 8 | Logs carry identifiers, kinds, counts and durations, never Kubernetes object content, tokens or request input | `apps/api/tests/test_security_checklist.py`, `test_clusters.py` (`test_token_never_appears_in_logs`), `agent/internal/collect/collector_test.go` (`TestAgentLogsNeverContainObjectContent`) | `api`, `agent` |
| 9 | The LLM cannot add findings, lower a severity, or propose a patch for another resource | `apps/api/tests/test_analyst.py` | `api` |
| 10 | Reports are inert documents (no scripts, no external resources) and carry no product name; the logo is a PNG or JPEG of at most 2048 x 2048 pixels | `apps/api/tests/test_reports.py`, `test_settings.py` | `api` |
| 11 | A GitHub installation can only be attached by a GitHub user who can see it, and the workspace is limited to the repositories that user can push to; pull requests are opened only on request and never carry a masking placeholder | `apps/api/tests/test_github.py` | `api` |
| 12 | The whole chain on a real cluster: planted secrets are in neither the snapshot, the findings nor the report | `apps/web/scripts/e2e-collect.mjs` | `e2e` |

## Manual item: Zero Data Retention for the external LLM

Automated tests cannot confirm a contract. Before `LLM_PROVIDER=anthropic` is used with
customer data, the account owner must confirm **in writing** that Zero Data Retention is
enabled for the API organization, and record it here:

| Provider | Model | ZDR confirmed by | Date | Evidence (ticket, e-mail, contract) |
| --- | --- | --- | --- | --- |
| _pending_ | | | | |

Until this row is filled in, run without an LLM (`LLM_PROVIDER=` empty: reports use the rule
engine and template texts) or with a local model (`LLM_PROVIDER=openai_compatible`), where
no data leaves the server. Masking stays on in every mode.

## Reporting a vulnerability

Please e-mail the maintainer privately instead of opening a public issue.
