"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { authClient } from "@/lib/auth-client";

export function InviteMemberForm({ organizationId }: { organizationId: string }) {
  const router = useRouter();
  const [pending, setPending] = useState(false);

  async function onSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    setPending(true);
    const result = await authClient.organization.inviteMember({
      organizationId,
      email: String(form.get("email")),
      role: form.get("role") === "admin" ? "admin" : "member",
    });
    setPending(false);
    if (result.error) {
      toast.error(result.error.message ?? "Could not create the invitation.");
      return;
    }
    formElement.reset();
    toast.success("Invitation created. Share the link with your teammate.");
    router.refresh();
  }

  return (
    <form onSubmit={onSubmit} className="flex flex-wrap items-end gap-3">
      <div className="flex flex-col gap-2">
        <Label htmlFor="invite-email">Email</Label>
        <Input id="invite-email" name="email" type="email" required className="w-72" />
      </div>
      <div className="flex flex-col gap-2">
        <Label htmlFor="invite-role">Role</Label>
        <select id="invite-role" name="role" className="h-8 rounded-lg border bg-background px-2 text-sm">
          <option value="member">member</option>
          <option value="admin">admin</option>
        </select>
      </div>
      <Button type="submit" disabled={pending}>
        {pending ? "Inviting\u2026" : "Invite"}
      </Button>
    </form>
  );
}
