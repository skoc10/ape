"use client";

import { useRouter } from "next/navigation";
import { useTransition } from "react";
import { toast } from "sonner";

import { authClient } from "@/lib/auth-client";

type Org = { id: string; name: string; slug: string };

const NEW = "__new__";

export function OrgSwitcher({ current, organizations }: { current: string; organizations: Org[] }) {
  const router = useRouter();
  const [pending, startTransition] = useTransition();

  function onChange(event: React.ChangeEvent<HTMLSelectElement>) {
    const value = event.target.value;
    if (value === NEW) {
      router.push("/onboarding");
      return;
    }
    const target = organizations.find((o) => o.slug === value);
    if (!target) return;
    startTransition(async () => {
      const result = await authClient.organization.setActive({ organizationId: target.id });
      if (result.error) {
        toast.error(result.error.message ?? "Could not switch workspace.");
        return;
      }
      router.push(`/${target.slug}/clusters`);
      router.refresh();
    });
  }

  return (
    <select
      aria-label="Workspace"
      className="h-8 rounded-lg border bg-background px-2 text-sm font-medium"
      value={current}
      onChange={onChange}
      disabled={pending}
    >
      {organizations.map((o) => (
        <option key={o.id} value={o.slug}>
          {o.name}
        </option>
      ))}
      <option value={NEW}>+ New workspace</option>
    </select>
  );
}
