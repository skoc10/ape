import { NextResponse, type NextRequest } from "next/server";

import { apiFetch } from "@/lib/api";
import { publicUrl } from "@/lib/public-url";
import { listOrganizations, requireSession } from "@/lib/session";

/**
 * Where GitHub sends people after installing the GitHub App or signing in for it.
 *
 * Three cases:
 * - no state: somebody installed the App from the plain link, usually an owner of the
 *   customer's organisation who has no account here. Nothing is connected; they get a
 *   page that says they are done.
 * - state and installation id: a workspace admin installed the App themselves.
 * - state only: a workspace admin signed in to connect an installation that exists.
 *
 * The state names the workspace the flow was started from; it is only read here to pick
 * that workspace, and verified (signature, user, expiry) by the API. The API also checks
 * with GitHub what the signed-in GitHub user can push to.
 */
export async function GET(request: NextRequest) {
  const params = request.nextUrl.searchParams;
  const code = params.get("code");
  const state = params.get("state");
  if (!state) return NextResponse.redirect(publicUrl("/github/installed", request.url));

  await requireSession();
  const home = publicUrl("/", request.url);
  if (!code) return NextResponse.redirect(home);

  let orgId: string | null = null;
  try {
    const payload = JSON.parse(Buffer.from(state.split(".")[1] ?? "", "base64url").toString("utf8")) as { org?: unknown };
    orgId = typeof payload.org === "string" ? payload.org : null;
  } catch {
    orgId = null;
  }
  const organization = (await listOrganizations()).find((o) => o.id === orgId);
  if (!organization) return NextResponse.redirect(home);
  const target = publicUrl(`/${organization.slug}/settings/github`, request.url);

  const rawInstallation = params.get("installation_id");
  if (rawInstallation !== null) {
    const installationId = Number(rawInstallation);
    if (!Number.isSafeInteger(installationId) || installationId <= 0) return NextResponse.redirect(home);
    const res = await apiFetch(organization.id, "/v1/settings/github/installation", {
      method: "POST",
      body: JSON.stringify({ installation_id: installationId, code, state }),
    });
    if (!res.ok) target.searchParams.set("error", String(res.status));
    return NextResponse.redirect(target);
  }

  const res = await apiFetch(organization.id, "/v1/settings/github/authorization", {
    method: "POST",
    body: JSON.stringify({ code, state }),
  });
  if (!res.ok) {
    target.searchParams.set("error", String(res.status));
    return NextResponse.redirect(target);
  }
  const body = (await res.json()) as { choices?: { installation_id: number; account: string }[] };
  if (body.choices && body.choices.length > 0) {
    // Only ids and account names travel in the URL; the page asks the API for the links.
    target.searchParams.set("choose", body.choices.map((c) => `${c.installation_id}:${c.account}`).join(","));
  }
  return NextResponse.redirect(target);
}
