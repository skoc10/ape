import { headers } from "next/headers";

import { AcceptInvitation } from "@/components/accept-invitation";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { auth } from "@/lib/auth";
import { requireSession } from "@/lib/session";

export default async function Page({ params }: PageProps<"/accept-invitation/[id]">) {
  const { id } = await params;
  const session = await requireSession(`/accept-invitation/${id}`);

  // Better Auth only returns the invitation to the invited email address.
  const invitation = await auth.api
    .getInvitation({ headers: await headers(), query: { id } })
    .catch(() => null);

  if (!invitation || invitation.status !== "pending") {
    return (
      <Card className="w-full max-w-sm">
        <CardHeader>
          <CardTitle>Invitation not available</CardTitle>
          <CardDescription>
            This invitation does not exist, has expired, or was sent to a different email address than{" "}
            {session.user.email}.
          </CardDescription>
        </CardHeader>
      </Card>
    );
  }

  return (
    <Card className="w-full max-w-sm">
      <CardHeader>
        <CardTitle>Join {invitation.organizationName}</CardTitle>
        <CardDescription>
          {invitation.inviterEmail} invited you as {invitation.role}.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <AcceptInvitation invitationId={invitation.id} orgSlug={invitation.organizationSlug} />
      </CardContent>
    </Card>
  );
}
