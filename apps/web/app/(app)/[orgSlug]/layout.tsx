import Link from "next/link";

import { OrgSwitcher } from "@/components/org-switcher";
import { SignOutButton } from "@/components/sign-out-button";
import { requireOrganization } from "@/lib/session";

export default async function OrgLayout({ children, params }: LayoutProps<"/[orgSlug]">) {
  const { orgSlug } = await params;
  const { session, organization, organizations } = await requireOrganization(orgSlug);

  return (
    <div className="flex flex-1 flex-col">
      <header className="flex flex-wrap items-center gap-4 border-b px-6 py-3">
        <OrgSwitcher
          current={organization.slug}
          organizations={organizations.map((o) => ({ id: o.id, name: o.name, slug: o.slug }))}
        />
        <nav className="flex gap-4 text-sm">
          <Link href={`/${organization.slug}/clusters`} className="hover:underline">
            Clusters
          </Link>
          <Link href={`/${organization.slug}/settings/members`} className="hover:underline">
            Members
          </Link>
          <Link href={`/${organization.slug}/settings/branding`} className="hover:underline">
            Branding
          </Link>
          <Link href={`/${organization.slug}/settings/github`} className="hover:underline">
            GitHub
          </Link>
        </nav>
        <div className="ml-auto flex items-center gap-3 text-sm text-muted-foreground">
          <span>{session.user.email}</span>
          <SignOutButton />
        </div>
      </header>
      <main className="mx-auto w-full max-w-5xl flex-1 p-6">{children}</main>
    </div>
  );
}
