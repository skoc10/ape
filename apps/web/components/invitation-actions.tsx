"use client";

import { useRouter } from "next/navigation";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { authClient } from "@/lib/auth-client";

export function InvitationActions({ invitationId, link }: { invitationId: string; link: string }) {
  const router = useRouter();
  return (
    <div className="flex justify-end gap-2">
      <Button
        variant="outline"
        size="sm"
        onClick={async () => {
          await navigator.clipboard.writeText(link);
          toast.success("Link copied.");
        }}
      >
        Copy link
      </Button>
      <Button
        variant="destructive"
        size="sm"
        onClick={async () => {
          const result = await authClient.organization.cancelInvitation({ invitationId });
          if (result.error) {
            toast.error(result.error.message ?? "Could not cancel the invitation.");
            return;
          }
          router.refresh();
        }}
      >
        Cancel
      </Button>
    </div>
  );
}
