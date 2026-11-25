import "server-only";

import { headers } from "next/headers";

import { auth } from "./auth";

const API_URL = process.env.API_URL ?? "http://localhost:8000";

function claimOrgId(token: string): string | null {
  const payload = token.split(".")[1];
  if (!payload) return null;
  const json = JSON.parse(Buffer.from(payload, "base64url").toString("utf8")) as { org_id?: string | null };
  return json.org_id ?? null;
}

/**
 * Returns a short-lived (15 min) JWT for the API whose `org_id` claim is
 * guaranteed to be `orgId`.
 *
 * The claim comes from the session's active organization. Another tab may have
 * switched it, so the active organization is re-pinned when needed and the
 * issued token is checked before it is used. Better Auth rejects the switch if
 * the user is not a member of `orgId`.
 */
export async function getApiToken(orgId: string): Promise<string> {
  const h = await headers();
  for (let attempt = 0; attempt < 2; attempt++) {
    const session = await auth.api.getSession({ headers: h });
    if (!session) throw new Error("not authenticated");
    if (session.session.activeOrganizationId !== orgId) {
      await auth.api.setActiveOrganization({ headers: h, body: { organizationId: orgId } });
    }
    const { token } = await auth.api.getToken({ headers: h });
    if (claimOrgId(token) === orgId) return token;
  }
  throw new Error("could not issue an API token for this organization");
}

/** Server-side fetch to the FastAPI backend on behalf of the current user and organization. */
export async function apiFetch(orgId: string, path: string, init: RequestInit = {}): Promise<Response> {
  const token = await getApiToken(orgId);
  const requestHeaders = new Headers(init.headers);
  requestHeaders.set("Authorization", `Bearer ${token}`);
  if (init.body && !requestHeaders.has("Content-Type")) {
    requestHeaders.set("Content-Type", "application/json");
  }
  return fetch(`${API_URL}${path}`, { ...init, headers: requestHeaders, cache: "no-store" });
}
