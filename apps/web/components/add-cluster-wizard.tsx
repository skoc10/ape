"use client";

import Link from "next/link";
import { useState, useTransition } from "react";

import { createClusterWithToken, type TokenResult } from "@/app/(app)/[orgSlug]/clusters/actions";
import { InstallInstructions } from "@/components/install-instructions";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

type Created = Extract<TokenResult, { ok: true }>;

export function AddClusterWizard({ orgSlug }: { orgSlug: string }) {
  const [created, setCreated] = useState<Created | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pending, startTransition] = useTransition();

  function onSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const name = String(new FormData(event.currentTarget).get("name")).trim();
    setError(null);
    startTransition(async () => {
      const result = await createClusterWithToken(orgSlug, name);
      if (result.ok) setCreated(result);
      else setError(result.error);
    });
  }

  if (created) {
    return (
      <Card className="max-w-2xl">
        <CardHeader>
          <CardDescription>Step 2 of 2</CardDescription>
          <CardTitle>Install the agent on {created.clusterName}</CardTitle>
        </CardHeader>
        <CardContent>
          <InstallInstructions token={created.token} command={created.command} />
        </CardContent>
        <CardFooter className="mt-4 flex gap-2">
          <Link href={`/${orgSlug}/clusters/${created.clusterId}`} className={buttonVariants()}>
            Done
          </Link>
        </CardFooter>
      </Card>
    );
  }

  return (
    <Card className="max-w-2xl">
      <CardHeader>
        <CardDescription>Step 1 of 2</CardDescription>
        <CardTitle>Name the cluster</CardTitle>
      </CardHeader>
      <form onSubmit={onSubmit}>
        <CardContent className="flex flex-col gap-4">
          {error && (
            <Alert variant="destructive">
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          )}
          <div className="flex flex-col gap-2">
            <Label htmlFor="name">Cluster name</Label>
            <Input
              id="name"
              name="name"
              placeholder="prod-eu-west"
              pattern="[a-z0-9]([a-z0-9\-]{0,61}[a-z0-9])?"
              title="Lowercase letters, digits and dashes"
              maxLength={63}
              required
            />
            <p className="text-xs text-muted-foreground">Lowercase letters, digits and dashes.</p>
          </div>
        </CardContent>
        <CardFooter className="mt-4 flex gap-2">
          <Button type="submit" disabled={pending}>
            {pending ? "Creating\u2026" : "Create cluster and token"}
          </Button>
          <Link href={`/${orgSlug}/clusters`} className={buttonVariants({ variant: "outline" })}>
            Cancel
          </Link>
        </CardFooter>
      </form>
    </Card>
  );
}
