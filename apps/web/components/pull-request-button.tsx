"use client";

import { useTransition } from "react";
import { toast } from "sonner";

import { openPullRequest } from "@/app/(app)/[orgSlug]/clusters/actions";
import { Button } from "@/components/ui/button";

export function PullRequestButton({ orgSlug, runId }: { orgSlug: string; runId: string }) {
  const [pending, startTransition] = useTransition();
  return (
    <Button
      disabled={pending}
      onClick={() =>
        startTransition(async () => {
          const result = await openPullRequest(orgSlug, runId);
          if (result.ok) {
            toast.success(`Pull request opened: ${result.applied} applied, ${result.manual} to apply manually.`);
          } else toast.error(result.error);
        })
      }
    >
      {pending ? "Opening\u2026" : "Open pull request"}
    </Button>
  );
}
