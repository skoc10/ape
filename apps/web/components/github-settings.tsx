"use client";

import { useTransition } from "react";
import { toast } from "sonner";

import { chooseGitHubRepo, disconnectGitHub } from "@/app/(app)/[orgSlug]/settings/actions";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

export function GitHubRepoForm({
  orgSlug,
  repos,
  repo,
  manifestPath,
}: {
  orgSlug: string;
  repos: { full_name: string }[];
  repo: string | null;
  manifestPath: string;
}) {
  const [pending, startTransition] = useTransition();
  return (
    <form
      className="flex max-w-xl flex-col gap-4"
      onSubmit={(event) => {
        event.preventDefault();
        const form = new FormData(event.currentTarget);
        startTransition(async () => {
          const result = await chooseGitHubRepo(orgSlug, form);
          if (result.ok) toast.success("Repository saved.");
          else toast.error(result.error);
        });
      }}
    >
      <div className="flex flex-col gap-2">
        <Label htmlFor="repo">Repository with the Kubernetes manifests</Label>
        <select id="repo" name="repo" defaultValue={repo ?? ""} required className="h-8 rounded-lg border bg-background px-2 text-sm">
          <option value="" disabled>
            Choose a repository
          </option>
          {repos.map((r) => (
            <option key={r.full_name} value={r.full_name}>
              {r.full_name}
            </option>
          ))}
        </select>
      </div>
      <div className="flex flex-col gap-2">
        <Label htmlFor="manifest_path">Manifest directory</Label>
        <Input id="manifest_path" name="manifest_path" defaultValue={manifestPath} placeholder="/k8s" />
        <p className="text-xs text-muted-foreground">Only .yaml and .yml files (also .yaml.tpl and .yml.tpl) below this directory are read.</p>
      </div>
      <div className="flex gap-2">
        <Button type="submit" disabled={pending}>
          Save
        </Button>
        <Button
          type="button"
          variant="outline"
          disabled={pending}
          onClick={() =>
            startTransition(async () => {
              const result = await disconnectGitHub(orgSlug);
              if (!result.ok) toast.error(result.error);
            })
          }
        >
          Disconnect
        </Button>
      </div>
    </form>
  );
}
