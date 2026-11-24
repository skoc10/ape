import "server-only";

import { headers } from "next/headers";
import { notFound, redirect } from "next/navigation";

import { auth } from "./auth";

export async function getSession() {
  return auth.api.getSession({ headers: await headers() });
}

/** Returns the session or redirects to the sign-in page. */
export async function requireSession(next?: string) {
  const session = await getSession();
  if (!session) {
    redirect(next ? `/sign-in?next=${encodeURIComponent(next)}` : "/sign-in");
  }
  return session;
}

export async function listOrganizations() {
  return auth.api.listOrganizations({ headers: await headers() });
}

/**
 * Resolves the organization in the URL. Better Auth only lists organizations
 * the user is a member of, so an unknown or foreign slug is a 404.
 */
export async function requireOrganization(orgSlug: string) {
  const session = await requireSession();
  const organizations = await listOrganizations();
  const organization = organizations.find((o) => o.slug === orgSlug);
  if (!organization) {
    notFound();
  }
  return { session, organization, organizations };
}
