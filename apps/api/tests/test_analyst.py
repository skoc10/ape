"""LLM enrichment (task 3.5), with scripted models instead of a real provider."""

import json
import os
import uuid
from pathlib import Path
from typing import Any

import psycopg
import pytest
import yaml
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models import Model
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.openai import OpenAIChatModel

from ape.analysis.analyst import SYSTEM_PROMPT, Analyst
from ape.analysis.model import build_model
from ape.analysis.pipeline import Pipeline
from ape.core.config import Settings
from ape.core.db import Database
from ape.observability.metrics import Metrics
from ape.rules import RULES, Finding, run_rules
from ape.sanitize import MaskedPayload
from ape.storage import ObjectStore

from .conftest import Tenant
from .test_worker import queue_run, run_row, vulnerable_objects

os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")

VULNERABLE = Path(__file__).resolve().parents[3] / "testdata" / "vulnerable"
PLANTED = (VULNERABLE / "planted-secrets.txt").read_text().split()


@pytest.fixture(autouse=True)
def quiet_queue(admin_db: psycopg.Connection[Any]) -> None:
    admin_db.execute(
        "update audit_runs set status = 'failed', error = 'parked by tests' "
        "where status in ('collected', 'analyzing', 'reporting')"
    )


@pytest.fixture
async def db(settings: Settings) -> Any:
    database = Database(settings.database_url, min_size=1, max_size=3)
    await database.open()
    yield database
    await database.close()


def prompt_text(messages: list[ModelMessage]) -> str:
    return json.dumps([str(part) for message in messages for part in message.parts])


def findings_in_prompt(messages: list[ModelMessage]) -> list[dict[str, Any]]:
    """Parses the JSON payload back out of the last user prompt."""
    for message in reversed(messages):
        for part in message.parts:
            content = getattr(part, "content", None)
            if isinstance(content, str) and '"findings"' in content:
                payload: dict[str, Any] = json.loads(content[content.index("{") :])
                listed: list[dict[str, Any]] = payload["findings"]
                return listed
    raise AssertionError("no findings payload in the prompt")


def answer(item: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    base = {
        "fingerprint": item["fingerprint"],
        "severity": item["severity"],
        "title": f"LLM: {item['title']}"[:120],
        "explanation": f"Explained {item['rule_id']} for {item['resource']['name']}.",
        "business_impact": "An attacker could take over the node.",
        "remediation": "1. Change the manifest.\n2. Roll out.",
        "patch": {
            "format": "strategic_merge",
            "target": item["resource"],
            "content": "spec:\n  template:\n    spec:\n      hostNetwork: false\n",
        },
        "confidence": 0.9,
    }
    return {**base, **overrides}


class Scripted:
    """A model whose answers are computed from the prompt; records everything it was sent."""

    def __init__(self, mutate: Any = None, fail_calls: set[int] | None = None) -> None:
        self.prompts: list[str] = []
        self.instructions: Any = None
        self.calls = 0
        self._mutate = mutate
        self._fail_calls = fail_calls or set()

    def model(self) -> Model:
        def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            self.calls += 1
            self.prompts.append(prompt_text(messages))
            self.instructions = info.instructions
            if self.calls in self._fail_calls:
                raise RuntimeError("provider exploded and echoed: " + self.prompts[-1][:50])
            items = findings_in_prompt(messages)
            findings = [answer(item) for item in items]
            if self._mutate:
                findings = self._mutate(findings, items)
            args = {
                "executive_summary": f"Batch {self.calls} looks risky.",
                "top_risks": [f"risk from batch {self.calls}"],
                "findings": findings,
            }
            return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, args)])

        return FunctionModel(respond)


def analyst(settings: Settings, db: Database, model: Model | None, **overrides: Any) -> Analyst:
    configured = settings.model_copy(
        update={"llm_provider": "test" if model else "", "llm_model": "scripted", **overrides}
    )
    return Analyst(configured, db, Metrics(), model)


# --- model factory -------------------------------------------------------------------


def test_model_factory_switches_on_environment_only(settings: Settings) -> None:
    assert build_model(settings) is None  # LLM off by default
    claude = build_model(
        settings.model_copy(update={"llm_provider": "anthropic", "llm_api_key": "test"})
    )
    assert isinstance(claude, AnthropicModel) and claude.model_name == "claude-sonnet-5-5"
    local = build_model(
        settings.model_copy(
            update={
                "llm_provider": "openai_compatible",
                "llm_base_url": "http://ollama:11434/v1",
                "llm_model": "qwen2.5-coder:32b",
            }
        )
    )
    assert isinstance(local, OpenAIChatModel) and local.model_name == "qwen2.5-coder:32b"
    assert str(local.client.base_url).startswith("http://ollama:11434/v1")
    with pytest.raises(ValueError, match="unknown LLM_PROVIDER"):
        build_model(settings.model_copy(update={"llm_provider": "skynet"}))


# --- analyse -------------------------------------------------------------------------


async def test_findings_are_enriched_in_batches(settings: Settings, db: Database) -> None:
    objects = vulnerable_objects()
    found = run_rules(objects)
    script = Scripted()
    result = await analyst(settings, db, script.model(), llm_batch_size=7).analyse(
        "org_x", objects, found
    )

    assert script.calls == 3 and result.batches == 3  # 18 findings in batches of 7
    assert set(result.enriched) == {f.fingerprint for f in found}
    assert result.input_tokens > 0 and result.output_tokens > 0
    assert result.top_risks == ["risk from batch 1", "risk from batch 2", "risk from batch 3"]
    assert SYSTEM_PROMPT[:60] in str(script.instructions)


async def test_nothing_unmasked_reaches_the_model(settings: Settings, db: Database) -> None:
    # Worst case: the snapshot was NOT masked (as if the first two locks had both failed).
    raw_objects: list[dict[str, Any]] = []
    for path in sorted((VULNERABLE / "manifests").glob("*.yaml")):
        raw_objects.extend(doc for doc in yaml.safe_load_all(path.read_text()) if doc)
    script = Scripted()
    await analyst(settings, db, script.model()).analyse(
        "org_x", raw_objects, run_rules(raw_objects)
    )

    sent = "\n".join(script.prompts)
    assert "storefront" in sent  # the manifests really are in the prompt
    assert [secret for secret in PLANTED if secret in sent] == []
    assert "<secret:" in sent


async def test_the_model_call_refuses_anything_but_a_masked_payload(
    settings: Settings, db: Database
) -> None:
    subject = analyst(settings, db, Scripted().model())
    with pytest.raises(TypeError, match="only masked data"):
        await subject._ask({"findings": []}, 1000)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        MaskedPayload({"findings": []}, object())


async def test_unknown_fingerprints_and_bad_patches_are_dropped(
    settings: Settings, db: Database
) -> None:
    objects = vulnerable_objects()
    found = run_rules(objects)

    def mutate(findings: list[dict[str, Any]], items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        findings[0]["patch"]["content"] = "spec: [unclosed"  # not YAML
        findings[1]["patch"]["target"] = {
            "kind": "Deployment",
            "namespace": "kube-system",
            "name": "coredns",
        }
        findings[2]["patch"]["content"] = "just a sentence"  # YAML, but not a mapping
        findings[3]["patch"] = None
        invented = answer(items[0], fingerprint="f" * 64, title="Invented by the model")
        return [*findings, invented]

    result = await analyst(settings, db, Scripted(mutate).model()).analyse("org_x", objects, found)
    assert result.dropped_unknown == 1
    assert "f" * 64 not in result.enriched
    assert result.dropped_patches == 3
    ordered = [result.enriched[f.fingerprint] for f in found]
    assert [e.patch is None for e in ordered[:5]] == [True, True, True, True, False]
    assert ordered[0].explanation.startswith("Explained")  # the text is kept, only the patch goes


async def test_a_failing_batch_does_not_stop_the_others(settings: Settings, db: Database) -> None:
    objects = vulnerable_objects()
    found = run_rules(objects)
    script = Scripted(fail_calls={2})  # the provider errors on the second batch
    result = await analyst(settings, db, script.model(), llm_batch_size=6).analyse(
        "org_x", objects, found
    )
    assert result.batches == 3 and result.failed_batches == 1
    assert len(result.enriched) == 12


async def test_token_budget_stops_further_batches(settings: Settings, db: Database) -> None:
    objects = vulnerable_objects()
    found = run_rules(objects)
    script = Scripted()
    result = await analyst(
        settings, db, script.model(), llm_batch_size=6, llm_max_tokens_per_run=50
    ).analyse("org_x", objects, found)
    # The first batch already exceeds the budget; nothing else is sent.
    assert script.calls == 1 and result.budget_exhausted
    assert result.enriched == {} and result.failed_batches == 0


async def test_llm_off_means_no_calls(settings: Settings, db: Database) -> None:
    objects = vulnerable_objects()
    result = await analyst(settings, db, None).analyse("org_x", objects, run_rules(objects))
    assert result.batches == 0 and result.enriched == {}


# --- through the worker pipeline ---------------------------------------------------------


async def run_pipeline(
    settings: Settings,
    db: Database,
    model: Model | None,
    tenant: Tenant,
    admin_db: psycopg.Connection[Any],
    tmp_path: Path,
) -> tuple[uuid.UUID, Metrics]:
    metrics = Metrics()
    configured = settings.model_copy(
        update={"llm_provider": "test" if model else "", "llm_model": "scripted"}
    )
    subject = Analyst(configured, db, metrics, model)
    pipeline = Pipeline(configured, db, ObjectStore(settings), metrics, enrich=subject.enrich_run)
    run_id = await queue_run(admin_db, settings, tenant, tmp_path, objects=vulnerable_objects())
    run = await pipeline.claim()
    assert run is not None
    assert await pipeline.process(run) == "completed"
    return run_id, metrics


def stored(admin_db: psycopg.Connection[Any], run_id: uuid.UUID) -> list[dict[str, Any]]:
    with admin_db.cursor(row_factory=psycopg.rows.dict_row) as cur:
        cur.execute(
            "select rule_id, severity, title, explanation, remediation, patch from findings "
            "where audit_run_id = %s order by rule_id, resource_name",
            (run_id,),
        )
        return cur.fetchall()


async def test_pipeline_stores_llm_output_and_token_usage(
    settings: Settings,
    db: Database,
    tenant_a: Tenant,
    admin_db: psycopg.Connection[Any],
    tmp_path: Path,
) -> None:
    run_id, metrics = await run_pipeline(
        settings, db, Scripted().model(), tenant_a, admin_db, tmp_path
    )
    rows = stored(admin_db, run_id)
    assert len(rows) == 18
    assert all(
        r["title"].startswith("LLM: ") and "Business impact:" in r["explanation"] for r in rows
    )
    assert all(r["patch"] and yaml.safe_load(r["patch"]) for r in rows)

    stats = run_row(admin_db, run_id)["stats"]
    assert (
        stats["llm"]["enabled"] is True
        and stats["llm"]["enriched"] == 18
        and stats["llm"]["templated"] == 0
    )
    assert stats["llm"]["input_tokens"] > 0 and stats["llm"]["output_tokens"] > 0
    assert stats["summary"] == "Batch 1 looks risky."
    assert "llm" in stats["durations"]
    value = metrics.registry.get_sample_value
    assert (
        value("ape_llm_tokens_total", {"model": "scripted", "direction": "input"})
        == stats["llm"]["input_tokens"]
    )
    assert (
        value("ape_llm_tokens_total", {"model": "scripted", "direction": "output"})
        == stats["llm"]["output_tokens"]
    )


async def test_without_an_llm_the_report_uses_template_text(
    settings: Settings,
    db: Database,
    tenant_a: Tenant,
    admin_db: psycopg.Connection[Any],
    tmp_path: Path,
) -> None:
    run_id, _ = await run_pipeline(settings, db, None, tenant_a, admin_db, tmp_path)
    rows = stored(admin_db, run_id)
    assert len(rows) == 18
    for row in rows:
        info = RULES[row["rule_id"]]
        assert (row["title"], row["explanation"], row["remediation"]) == (
            info.title,
            info.explanation,
            info.remediation,
        )
        assert row["patch"] is None
    stats = run_row(admin_db, run_id)["stats"]
    assert stats["llm"] == {
        "enabled": False,
        "provider": None,
        "model": None,
        "input_tokens": 0,
        "output_tokens": 0,
        "batches": 0,
        "failed_batches": 0,
        "enriched": 0,
        "templated": 18,
        "dropped_unknown_findings": 0,
        "dropped_patches": 0,
        "budget_exhausted": False,
    }
    assert stats["summary"].startswith("The audit found 18 issues (3 critical")
    assert len(stats["top_risks"]) == 5 and stats["top_risks"][0].startswith(
        ("Binding to cluster-admin", "Privileged", "Wildcard")
    )


async def test_an_unreachable_llm_falls_back_to_templates_and_the_audit_completes(
    settings: Settings,
    db: Database,
    tenant_a: Tenant,
    admin_db: psycopg.Connection[Any],
    tmp_path: Path,
) -> None:
    always_down = Scripted(fail_calls=set(range(1, 50)))
    run_id, _ = await run_pipeline(settings, db, always_down.model(), tenant_a, admin_db, tmp_path)
    rows = stored(admin_db, run_id)
    assert all(r["explanation"] == RULES[r["rule_id"]].explanation for r in rows)
    stats = run_row(admin_db, run_id)["stats"]["llm"]
    assert stats["enabled"] is True and stats["failed_batches"] == 1 and stats["templated"] == 18


def test_finding_type_is_shared_with_the_rule_engine() -> None:
    assert Finding.__module__ == "ape.rules.model"
