import { headers } from "next/headers";

import { InviteMemberForm } from "@/components/invite-member-form";
import { InvitationActions } from "@/components/invitation-actions";
import { Badge } from "@/components/ui/badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { auth } from "@/lib/auth";
import { requireOrganization } from "@/lib/session";

export default async function Page({ params }: PageProps<"/[orgSlug]/settings/members">) {
  const { orgSlug } = await params;
  const { session, organization } = await requireOrganization(orgSlug);
  const h = await headers();
  const full = await auth.api.getFullOrganization({ headers: h, query: { organizationId: organization.id } });
  const members = full?.members ?? [];
  const invitations = (full?.invitations ?? []).filter((i) => i.status === "pending");
  const myRole = members.find((m) => m.userId === session.user.id)?.role ?? "member";
  const canInvite = myRole === "owner" || myRole === "admin";
  const baseUrl = process.env.BETTER_AUTH_URL ?? "http://localhost:3000";

  return (
    <div className="flex flex-col gap-8">
      <section className="flex flex-col gap-3">
        <h1 className="text-xl font-semibold">Members</h1>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Name</TableHead>
              <TableHead>Email</TableHead>
              <TableHead>Role</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {members.map((m) => (
              <TableRow key={m.id}>
                <TableCell>{m.user.name}</TableCell>
                <TableCell>{m.user.email}</TableCell>
                <TableCell>
                  <Badge variant="secondary">{m.role}</Badge>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </section>

      {canInvite && (
        <section className="flex flex-col gap-3">
          <h2 className="text-lg font-semibold">Invite a member</h2>
          <InviteMemberForm organizationId={organization.id} />
          {invitations.length > 0 && (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Email</TableHead>
                  <TableHead>Role</TableHead>
                  <TableHead>Invitation link</TableHead>
                  <TableHead />
                </TableRow>
              </TableHeader>
              <TableBody>
                {invitations.map((i) => (
                  <TableRow key={i.id}>
                    <TableCell>{i.email}</TableCell>
                    <TableCell>
                      <Badge variant="secondary">{i.role}</Badge>
                    </TableCell>
                    <TableCell className="font-mono text-xs">{`${baseUrl}/accept-invitation/${i.id}`}</TableCell>
                    <TableCell className="text-right">
                      <InvitationActions invitationId={i.id} link={`${baseUrl}/accept-invitation/${i.id}`} />
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </section>
      )}
    </div>
  );
}
