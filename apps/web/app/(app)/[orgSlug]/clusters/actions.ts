"use server";

import { revalidatePath } from "next/cache";

import { apiFetch } from "@/lib/api";
import { helmInstallCommand, isUuid } from "@/lib/clusters";
import { requireOrganization } from "@/lib/session";

type Failure = { ok: false; error: string };
export type TokenResult =
  | { ok: true; clusterId: string; clusterName: string; token: string; prefix: string; command: string }
  | Failure;
export type SimpleResult = { ok: true } | Failure;

async function errorMessage(res: Response): Promise<string> {
  const body = (await res.json().catch(() => null)) as { detail?: unknown } | null;
  if (res.status === 422) return "Use lowercase letters, digits and dashes (max 63 characters).";
  return typeof body?.detail === "string" ? body.detail : `Request failed (${res.status}).`;
}

async function issueToken(orgId: string, clusterId: string, clusterName: string): Promise<TokenResult> {
  const res = await apiFetch(orgId, `/v1/clusters/${clusterId}/tokens`, { method: "POST" });
  if (!res.ok) return { ok: false, error: await errorMessage(res) };
  const created = (await res.json()) as { token: string; prefix: string };
  return {
    ok: true,
    clusterId,
    clusterName,
    token: created.token,
    prefix: created.prefix,
    command: helmInstallCommand(created.token),
  };
}

/** Wizard step 1: creates the cluster and its first agent token. */
export async function createClusterWithToken(orgSlug: string, name: string): Promise<TokenResult> {
  // Every action re-checks the session and the membership; arguments are never trusted.
  const { organization } = await requireOrganization(orgSlug);
  const res = await apiFetch(organization.id, "/v1/clusters", {
    method: "POST",
    body: JSON.stringify({ name }),
  });
  if (!res.ok) return { ok: false, error: await errorMessage(res) };
  const cluster = (await res.json()) as { id: string; name: string };
  revalidatePath(`/${orgSlug}/clusters`);
  return issueToken(organization.id, cluster.id, cluster.name);
}

export async function createToken(orgSlug: string, clusterId: string): Promise<TokenResult> {
  const { organization } = await requireOrganization(orgSlug);
  if (!isUuid(clusterId)) return { ok: false, error: "cluster not found" };
  const result = await issueToken(organization.id, clusterId, "");
  revalidatePath(`/${orgSlug}/clusters/${clusterId}`);
  return result;
}

export async function revokeToken(orgSlug: string, clusterId: string, tokenId: string): Promise<SimpleResult> {
  const { organization } = await requireOrganization(orgSlug);
  if (!isUuid(clusterId) || !isUuid(tokenId)) return { ok: false, error: "token not found" };
  const res = await apiFetch(organization.id, `/v1/clusters/${clusterId}/tokens/${tokenId}`, {
    method: "DELETE",
  });
  if (!res.ok) return { ok: false, error: await errorMessage(res) };
  revalidatePath(`/${orgSlug}/clusters/${clusterId}`);
  return { ok: true };
}

export async function startAudit(orgSlug: string, clusterId: string): Promise<SimpleResult> {
  const { organization } = await requireOrganization(orgSlug);
  if (!isUuid(clusterId)) return { ok: false, error: "cluster not found" };
  const res = await apiFetch(organization.id, `/v1/clusters/${clusterId}/audits`, { method: "POST" });
  if (!res.ok) return { ok: false, error: await errorMessage(res) };
  revalidatePath(`/${orgSlug}/clusters/${clusterId}`);
  return { ok: true };
}
