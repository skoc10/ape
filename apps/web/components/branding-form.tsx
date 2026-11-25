"use client";

import { useState, useTransition } from "react";
import { toast } from "sonner";

import { removeLogo, saveBranding } from "@/app/(app)/[orgSlug]/settings/actions";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

const DEFAULT_COLOR = "#1f6feb";

export function BrandingForm({
  orgSlug,
  companyName,
  primaryColor,
  logo,
  canManage,
}: {
  orgSlug: string;
  companyName: string;
  primaryColor: string | null;
  logo: string | null;
  canManage: boolean;
}) {
  const [color, setColor] = useState(primaryColor ?? DEFAULT_COLOR);
  const [pending, startTransition] = useTransition();

  function onSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    form.set("primary_color", color);
    startTransition(async () => {
      const result = await saveBranding(orgSlug, form);
      if (result.ok) toast.success("Branding saved. New reports will use it.");
      else toast.error(result.error);
    });
  }

  return (
    <form onSubmit={onSubmit} className="flex max-w-xl flex-col gap-5">
      <div className="flex flex-col gap-2">
        <Label htmlFor="company_name">Company name on reports</Label>
        <Input id="company_name" name="company_name" defaultValue={companyName} maxLength={80} disabled={!canManage} />
      </div>
      <div className="flex flex-col gap-2">
        <Label htmlFor="primary_color">Primary colour</Label>
        <div className="flex items-center gap-3">
          <input
            id="primary_color"
            type="color"
            value={color}
            onChange={(event) => setColor(event.target.value)}
            disabled={!canManage}
            className="h-8 w-12 rounded border"
          />
          <code className="text-sm">{color}</code>
        </div>
      </div>
      <div className="flex flex-col gap-2">
        <Label htmlFor="logo">Logo (PNG or JPEG, up to 512 KB)</Label>
        {logo && (
          <div className="flex items-center gap-3">
            {/* eslint-disable-next-line @next/next/no-img-element -- a data URI of the tenant's own logo */}
            <img src={logo} alt="Current logo" className="h-12 max-w-48 rounded border bg-white object-contain p-1" />
            {canManage && (
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={pending}
                onClick={() =>
                  startTransition(async () => {
                    const result = await removeLogo(orgSlug);
                    if (!result.ok) toast.error(result.error);
                  })
                }
              >
                Remove
              </Button>
            )}
          </div>
        )}
        <Input id="logo" name="logo" type="file" accept="image/png,image/jpeg" disabled={!canManage} />
      </div>
      {canManage && (
        <Button type="submit" disabled={pending} className="self-start">
          {pending ? "Saving\u2026" : "Save branding"}
        </Button>
      )}
    </form>
  );
}
