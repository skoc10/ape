import { redirect } from "next/navigation";

import { listOrganizations, requireSession } from "@/lib/session";

export default async function Home() {
  const session = await requireSession();
  const organizations = await listOrganizations();
  if (organizations.length === 0) {
    redirect("/onboarding");
  }
  const active = organizations.find((o) => o.id === session.session.activeOrganizationId) ?? organizations[0];
  redirect(`/${active.slug}/clusters`);
}
