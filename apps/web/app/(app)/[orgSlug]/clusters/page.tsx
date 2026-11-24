import { requireOrganization } from "@/lib/session";

export default async function Page({ params }: PageProps<"/[orgSlug]/clusters">) {
  const { orgSlug } = await params;
  const { organization } = await requireOrganization(orgSlug);
  return (
    <section className="flex flex-col gap-2">
      <h1 className="text-xl font-semibold">Clusters</h1>
      <p className="text-sm text-muted-foreground">No clusters in {organization.name} yet.</p>
    </section>
  );
}
