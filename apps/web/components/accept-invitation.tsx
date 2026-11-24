"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { authClient } from "@/lib/auth-client";

export function AcceptInvitation({ invitationId, orgSlug }: { invitationId: string; orgSlug: string }) {
  const router = useRouter();
  const [pending, setPending] = useState(false);

  async function respond(accept: boolean) {
    setPending(true);
    const result = accept
      ? await authClient.organization.acceptInvitation({ invitationId })
      : await authClient.organization.rejectInvitation({ invitationId });
    setPending(false);
    if (result.error) {
      toast.error(result.error.message ?? "Could not respond to the invitation.");
      return;
    }
    router.push(accept ? `/${orgSlug}/clusters` : "/");
    router.refresh();
  }

  return (
    <div className="flex gap-2">
      <Button onClick={() => respond(true)} disabled={pending}>
        Accept
      </Button>
      <Button variant="outline" onClick={() => respond(false)} disabled={pending}>
        Decline
      </Button>
    </div>
  );
}
