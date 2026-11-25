import Link from "next/link";
import { notFound } from "next/navigation";

import { AuditPanel } from "@/components/audit-panel";
import { ClusterStatus } from "@/components/cluster-status";
import { TokenPanel } from "@/components/token-panel";
import { getCluster, listAudits, listClusters, listTokens } from "@/lib/clusters";
import { requireOrganization } from "@/lib/session";

export default async function Page({ params }: PageProps<"/[orgSlug]/clusters/[clusterId]">) {
  const { orgSlug, clusterId } = await params;
  const { organization } = await requireOrganization(orgSlug);
  const cluster = await getCluster(organization.id, clusterId);
  if (!cluster) notFound();
  // Sequential on purpose: each call may re-pin the session's active organization.
  const { can_manage: canManage } = await listClusters(organization.id);
  const [tokens, audits] = await Promise.all([
    listTokens(organization.id, cluster.id),
    listAudits(organization.id, cluster.id),
  ]);

  return (
    <div className="flex flex-col gap-8">
      <section className="flex flex-col gap-2">
        <Link href={`/${orgSlug}/clusters`} className="text-sm text-muted-foreground hover:underline">
          &larr; Clusters
        </Link>
        <div className="flex items-center gap-3">
          <h1 className="text-xl font-semibold">{cluster.name}</h1>
          <ClusterStatus status={cluster.status} />
        </div>
        <p className="text-sm text-muted-foreground">
          Kubernetes {cluster.k8s_version ?? "unknown"} &middot; agent {cluster.agent_version ?? "not connected yet"}
        </p>
      </section>
      <AuditPanel
        orgSlug={orgSlug}
        clusterId={cluster.id}
        connected={cluster.status === "connected"}
        canManage={canManage}
        runs={audits.map((run) => ({
          id: run.id,
          status: run.status,
          objects: run.stats.objects ?? null,
          findings: run.stats.findings?.total ?? null,
          redactions: run.stats.redactions
            ? Object.values(run.stats.redactions).reduce((sum, n) => sum + n, 0)
            : null,
          truncated: run.stats.truncated ?? false,
          error: run.error,
          created_at: run.created_at,
        }))}
      />
      <TokenPanel
        orgSlug={orgSlug}
        clusterId={cluster.id}
        canManage={canManage}
        tokens={tokens.map((t) => ({
          id: t.id,
          prefix: t.prefix,
          expires_at: t.expires_at,
          revoked_at: t.revoked_at,
          last_used_at: t.last_used_at,
          created_at: t.created_at,
        }))}
      />
    </div>
  );
}
