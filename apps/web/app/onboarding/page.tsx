import { CreateOrganizationForm } from "@/components/create-organization-form";
import { requireSession } from "@/lib/session";

export default async function Page() {
  await requireSession("/onboarding");
  return (
    <main className="flex flex-1 items-center justify-center bg-muted/40 p-6">
      <CreateOrganizationForm />
    </main>
  );
}
