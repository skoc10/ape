import { CreateOrganizationForm } from "@/components/create-organization-form";
import { ThemeToggle } from "@/components/theme-toggle";
import { requireSession } from "@/lib/session";

export default async function Page() {
  await requireSession("/onboarding");
  return (
    <main className="relative flex flex-1 items-center justify-center bg-muted/40 p-6">
      <ThemeToggle className="absolute top-4 right-4" />
      <CreateOrganizationForm />
    </main>
  );
}
