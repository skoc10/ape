import "server-only";

import { apiFetch } from "./api";

export type Cluster = {
  id: string;
  name: string;
  status: "pending" | "connected" | "offline";
  k8s_version: string | null;
  agent_version: string | null;
  last_seen_at: string | null;
  created_at: string;
};

export type AgentToken = {
  id: string;
  cluster_id: string;
  prefix: string;
  created_by: string;
  expires_at: string | null;
  revoked_at: string | null;
  last_used_at: string | null;
  created_at: string;
};

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** Ids are interpolated into API paths, so anything that is not a UUID is rejected first. */
export function isUuid(value: string): boolean {
  return UUID.test(value);
}

export async function listClusters(orgId: string): Promise<{ items: Cluster[]; can_manage: boolean }> {
  const res = await apiFetch(orgId, "/v1/clusters");
  if (!res.ok) throw new Error(`API error ${res.status}`);
  return res.json();
}

export async function getCluster(orgId: string, clusterId: string): Promise<Cluster | null> {
  if (!isUuid(clusterId)) return null;
  const res = await apiFetch(orgId, `/v1/clusters/${clusterId}`);
  if (res.status === 404) return null;
  if (!res.ok) throw new Error(`API error ${res.status}`);
  return res.json();
}

export async function listTokens(orgId: string, clusterId: string): Promise<AgentToken[]> {
  if (!isUuid(clusterId)) return [];
  const res = await apiFetch(orgId, `/v1/clusters/${clusterId}/tokens`);
  if (!res.ok) throw new Error(`API error ${res.status}`);
  return res.json();
}

/** The copy-paste command shown once, right after a token is created. */
export function helmInstallCommand(token: string): string {
  const chart = process.env.AGENT_CHART_REF ?? "oci://ghcr.io/skoc10/charts/ape-agent";
  const endpoint = process.env.AGENT_ENDPOINT ?? "ws://localhost:8000/v1/agent";
  return [
    `helm install ape-agent ${chart}`,
    "  -n ape-system --create-namespace",
    `  --set token=${token}`,
    `  --set endpoint=${endpoint}`,
  ].join(" \\\n");
}
