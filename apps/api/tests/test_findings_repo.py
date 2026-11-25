"""Findings storage and the "recurring" flag (task 3.3)."""

import uuid
from typing import Any

import psycopg

from ape.core.config import Settings
from ape.core.db import Database
from ape.repos import findings as findings_repo
from ape.rules import Finding, ResourceRef

from .conftest import Tenant


def finding(rule_id: str, name: str, severity: str = "high") -> Finding:
    return Finding(
        rule_id,
        "security",
        severity,
        f"{rule_id} title",
        ResourceRef("Deployment", "shop", name),
        {"n": 1},
    )  # type: ignore[arg-type]


def make_run(
    admin_db: psycopg.Connection[Any], tenant: Tenant, cluster_id: uuid.UUID, age_minutes: int
) -> uuid.UUID:
    run_id = uuid.uuid4()
    admin_db.execute(
        "insert into audit_runs (id, org_id, cluster_id, triggered_by, status, created_at) "
        "values (%s, %s, %s, %s, 'collected', now() - make_interval(mins => %s))",
        (run_id, tenant.org_id, cluster_id, tenant.user_id, age_minutes),
    )
    return run_id


def make_cluster(admin_db: psycopg.Connection[Any], tenant: Tenant, name: str) -> uuid.UUID:
    cluster_id = uuid.uuid4()
    admin_db.execute(
        "insert into clusters (id, org_id, name) values (%s, %s, %s)",
        (cluster_id, tenant.org_id, name),
    )
    return cluster_id


async def test_findings_are_stored_sorted_and_flagged_as_recurring(
    settings: Settings, tenant_a: Tenant, tenant_b: Tenant, admin_db: psycopg.Connection[Any]
) -> None:
    cluster = make_cluster(admin_db, tenant_a, "c1")
    other_cluster = make_cluster(admin_db, tenant_a, "c2")
    first = make_run(admin_db, tenant_a, cluster, age_minutes=60)
    second = make_run(admin_db, tenant_a, cluster, age_minutes=0)
    elsewhere = make_run(admin_db, tenant_a, other_cluster, age_minutes=120)
    cluster_b = make_cluster(admin_db, tenant_b, "c1")
    run_b = make_run(admin_db, tenant_b, cluster_b, age_minutes=120)

    db = Database(settings.database_url, min_size=1, max_size=2)
    await db.open()
    try:
        async with db.tenant(tenant_a.org_id) as tc:
            assert await findings_repo.insert_many(tc, first, [finding("SEC-001", "web")]) == 1
            # Same fingerprint in another cluster of the tenant: must not count as a repeat.
            await findings_repo.insert_many(tc, elsewhere, [finding("SEC-002", "api")])
            new = [
                finding("SEC-001", "web"),
                finding("SEC-002", "api", "critical"),
                finding("REL-001", "web", "low"),
            ]
            assert await findings_repo.insert_many(tc, second, new) == 3
            assert await findings_repo.insert_many(tc, second, new) == 0  # idempotent
        async with db.tenant(tenant_b.org_id) as tc:
            await findings_repo.insert_many(tc, run_b, [finding("SEC-002", "api")])

        async with db.tenant(tenant_a.org_id) as tc:
            rows = await findings_repo.list_for_run(tc, second)
            assert [(r["rule_id"], r["severity"], r["recurring"]) for r in rows] == [
                ("SEC-002", "critical", False),
                ("SEC-001", "high", True),
                ("REL-001", "low", False),
            ]
            assert rows[0]["evidence"] == {"n": 1}
            assert [r["recurring"] for r in await findings_repo.list_for_run(tc, first)] == [False]
            assert [
                r["rule_id"] for r in await findings_repo.list_for_run(tc, second, severity="low")
            ] == ["REL-001"]
            assert await findings_repo.list_for_run(tc, second, category="rbac") == []
            assert await findings_repo.list_for_run(tc, run_b) == []  # another tenant's run

            ok = await findings_repo.set_enrichment(
                tc,
                second,
                rows[1]["fingerprint"],
                severity="medium",
                title="t",
                explanation="e",
                remediation="r",
                patch=None,
            )
            assert ok
            assert not await findings_repo.set_enrichment(
                tc,
                second,
                "0" * 64,
                severity="low",
                title="t",
                explanation="e",
                remediation="r",
                patch=None,
            )
    finally:
        await db.close()
