import Link from "next/link";

import { ClusterStatus } from "@/components/cluster-status";
import { buttonVariants } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { listClusters } from "@/lib/clusters";
import { requireOrganization } from "@/lib/session";

export default async function Page({ params }: PageProps<"/[orgSlug]/clusters">) {
  const { orgSlug } = await params;
  const { organization } = await requireOrganization(orgSlug);
  const { items, can_manage: canManage } = await listClusters(organization.id);

  return (
    <section className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">Clusters</h1>
        {canManage && (
          <Link href={`/${orgSlug}/clusters/new`} className={buttonVariants()}>
            Add cluster
          </Link>
        )}
      </div>
      {items.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          No clusters in {organization.name} yet. Add one to get an agent token and the install command.
        </p>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Name</TableHead>
              <TableHead>Status</TableHead>
              <TableHead>Kubernetes</TableHead>
              <TableHead>Agent</TableHead>
              <TableHead>Last seen</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {items.map((cluster) => (
              <TableRow key={cluster.id}>
                <TableCell>
                  <Link href={`/${orgSlug}/clusters/${cluster.id}`} className="font-medium hover:underline">
                    {cluster.name}
                  </Link>
                </TableCell>
                <TableCell>
                  <ClusterStatus status={cluster.status} />
                </TableCell>
                <TableCell>{cluster.k8s_version ?? "\u2014"}</TableCell>
                <TableCell>{cluster.agent_version ?? "\u2014"}</TableCell>
                <TableCell>
                  {cluster.last_seen_at
                    ? new Date(cluster.last_seen_at).toISOString().slice(0, 16).replace("T", " ") + " UTC"
                    : "never"}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </section>
  );
}
