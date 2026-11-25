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

export const SEVERITIES = ["critical", "high", "medium", "low", "info"] as const;
export const CATEGORIES = ["security", "rbac", "network", "cost", "reliability"] as const;
export type Severity = (typeof SEVERITIES)[number];
export type Category = (typeof CATEGORIES)[number];

export type AuditFinding = {
  id: string;
  rule_id: string;
  category: Category;
  severity: Severity;
  title: string;
  resource_kind: string;
  resource_ns: string | null;
  resource_name: string;
  explanation: string | null;
  remediation: string | null;
  patch: string | null;
  recurring: boolean;
};

export type AuditRun = {
  id: string;
  cluster_id: string;
  status: "queued" | "collecting" | "collected" | "analyzing" | "reporting" | "completed" | "failed";
  stats: {
    objects?: number;
    truncated?: boolean;
    redactions?: Record<string, number>;
    findings?: { total: number } & Partial<Record<Severity, number>>;
    summary?: string;
    top_risks?: string[];
    llm?: { enabled: boolean; model: string | null; enriched: number; templated: number };
  };
  error: string | null;
  has_snapshot: boolean;
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

export async function listAudits(orgId: string, clusterId: string): Promise<AuditRun[]> {
  if (!isUuid(clusterId)) return [];
  const res = await apiFetch(orgId, `/v1/clusters/${clusterId}/audits`);
  if (!res.ok) throw new Error(`API error ${res.status}`);
  return res.json();
}

export async function getAudit(orgId: string, runId: string): Promise<AuditRun | null> {
  if (!isUuid(runId)) return null;
  const res = await apiFetch(orgId, `/v1/audits/${runId}`);
  if (res.status === 404) return null;
  if (!res.ok) throw new Error(`API error ${res.status}`);
  return res.json();
}

export async function listFindings(
  orgId: string,
  runId: string,
  filter: { severity?: Severity; category?: Category },
): Promise<AuditFinding[]> {
  if (!isUuid(runId)) return [];
  const query = new URLSearchParams();
  if (filter.severity) query.set("severity", filter.severity);
  if (filter.category) query.set("category", filter.category);
  const suffix = query.size ? `?${query}` : "";
  const res = await apiFetch(orgId, `/v1/audits/${runId}/findings${suffix}`);
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
