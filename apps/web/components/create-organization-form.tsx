"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { authClient } from "@/lib/auth-client";
import { isValidOrgSlug } from "@/lib/slug";

function slugify(value: string): string {
  return value
    .toLowerCase()
    .replaceAll("\u0131", "i") // dotless i has no Unicode decomposition
    .normalize("NFKD")
    .replace(/\p{M}+/gu, "") // drop combining marks left by the decomposition
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 48);
}

export function CreateOrganizationForm() {
  const router = useRouter();
  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  const [slugEdited, setSlugEdited] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  const effectiveSlug = slugEdited ? slug : slugify(name);

  async function onSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    if (!isValidOrgSlug(effectiveSlug)) {
      setError("Please choose a different URL name.");
      return;
    }
    setPending(true);
    const created = await authClient.organization.create({ name: name.trim(), slug: effectiveSlug });
    if (created.error) {
      setPending(false);
      setError(created.error.message ?? "Could not create the workspace.");
      return;
    }
    await authClient.organization.setActive({ organizationId: created.data.id });
    router.push(`/${effectiveSlug}/clusters`);
    router.refresh();
  }

  return (
    <Card className="w-full max-w-sm">
      <CardHeader>
        <CardTitle>Create a customer workspace</CardTitle>
        <CardDescription>Each customer gets an isolated workspace with its own clusters and reports.</CardDescription>
      </CardHeader>
      <form onSubmit={onSubmit}>
        <CardContent className="flex flex-col gap-4">
          {error && (
            <Alert variant="destructive">
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          )}
          <div className="flex flex-col gap-2">
            <Label htmlFor="name">Customer name</Label>
            <Input id="name" value={name} onChange={(e) => setName(e.target.value)} maxLength={80} required />
          </div>
          <div className="flex flex-col gap-2">
            <Label htmlFor="slug">URL name</Label>
            <Input
              id="slug"
              value={effectiveSlug}
              onChange={(e) => {
                setSlugEdited(true);
                setSlug(slugify(e.target.value));
              }}
              required
            />
          </div>
        </CardContent>
        <CardFooter className="mt-4">
          <Button type="submit" className="w-full" disabled={pending}>
            {pending ? "Creating\u2026" : "Create workspace"}
          </Button>
        </CardFooter>
      </form>
    </Card>
  );
}
