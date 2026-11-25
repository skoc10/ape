"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";

function Select({ name, label, options }: { name: string; label: string; options: readonly string[] }) {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  return (
    <label className="flex items-center gap-2 text-sm">
      <span className="text-muted-foreground">{label}</span>
      <select
        aria-label={label}
        className="h-8 rounded-lg border bg-background px-2 text-sm"
        value={params.get(name) ?? ""}
        onChange={(event) => {
          const next = new URLSearchParams(params);
          if (event.target.value) next.set(name, event.target.value);
          else next.delete(name);
          router.replace(next.size ? `${pathname}?${next}` : pathname);
        }}
      >
        <option value="">All</option>
        {options.map((option) => (
          <option key={option} value={option}>
            {option}
          </option>
        ))}
      </select>
    </label>
  );
}

export function FindingFilters({
  severities,
  categories,
}: {
  severities: readonly string[];
  categories: readonly string[];
}) {
  return (
    <div className="flex flex-wrap gap-4">
      <Select name="severity" label="Severity" options={severities} />
      <Select name="category" label="Category" options={categories} />
    </div>
  );
}
