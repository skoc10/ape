import { AddClusterWizard } from "@/components/add-cluster-wizard";
import { requireOrganization } from "@/lib/session";

export default async function Page({ params }: PageProps<"/[orgSlug]/clusters/new">) {
  const { orgSlug } = await params;
  await requireOrganization(orgSlug);
  return (
    <section className="flex flex-col gap-4">
      <h1 className="text-xl font-semibold">Add cluster</h1>
      <AddClusterWizard orgSlug={orgSlug} />
    </section>
  );
}
