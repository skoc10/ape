import { NextResponse, type NextRequest } from "next/server";

import { apiFetch } from "@/lib/api";
import { listOrganizations, requireSession } from "@/lib/session";

/**
 * Where GitHub sends the user after installing the GitHub App.
 *
 * The state names the workspace the flow was started from; it is only read here to
 * pick that workspace, and verified (signature, user, expiry) by the API. The API also
 * checks with GitHub that the signed-in GitHub user controls the installation.
 */
export async function GET(request: NextRequest) {
  await requireSession();
  const params = request.nextUrl.searchParams;
  const installationId = Number(params.get("installation_id"));
  const code = params.get("code");
  const state = params.get("state");
  const home = new URL("/", request.url);
  if (!Number.isSafeInteger(installationId) || installationId <= 0 || !code || !state) {
    return NextResponse.redirect(home);
  }

  let orgId: string | null = null;
  try {
    const payload = JSON.parse(Buffer.from(state.split(".")[1] ?? "", "base64url").toString("utf8")) as { org?: unknown };
    orgId = typeof payload.org === "string" ? payload.org : null;
  } catch {
    orgId = null;
  }
  const organization = (await listOrganizations()).find((o) => o.id === orgId);
  if (!organization) return NextResponse.redirect(home);

  const res = await apiFetch(organization.id, "/v1/settings/github/installation", {
    method: "POST",
    body: JSON.stringify({ installation_id: installationId, code, state }),
  });
  const target = new URL(`/${organization.slug}/settings/github`, request.url);
  if (!res.ok) target.searchParams.set("error", String(res.status));
  return NextResponse.redirect(target);
}
