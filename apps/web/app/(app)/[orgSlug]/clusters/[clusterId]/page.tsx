import Link from "next/link";
import { notFound } from "next/navigation";

import { ClusterStatus } from "@/components/cluster-status";
import { TokenPanel } from "@/components/token-panel";
import { getCluster, listClusters, listTokens } from "@/lib/clusters";
import { requireOrganization } from "@/lib/session";

export default async function Page({ params }: PageProps<"/[orgSlug]/clusters/[clusterId]">) {
  const { orgSlug, clusterId } = await params;
  const { organization } = await requireOrganization(orgSlug);
  const cluster = await getCluster(organization.id, clusterId);
  if (!cluster) notFound();
  const [tokens, { can_manage: canManage }] = await Promise.all([
    listTokens(organization.id, cluster.id),
    listClusters(organization.id),
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
