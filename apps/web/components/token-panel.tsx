"use client";

import { useState, useTransition } from "react";
import { toast } from "sonner";

import { createToken, revokeToken, type TokenResult } from "@/app/(app)/[orgSlug]/clusters/actions";
import { InstallInstructions } from "@/components/install-instructions";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";

type Token = {
  id: string;
  prefix: string;
  expires_at: string | null;
  revoked_at: string | null;
  last_used_at: string | null;
  created_at: string;
};

function formatDate(value: string | null): string {
  return value ? new Date(value).toISOString().slice(0, 16).replace("T", " ") + " UTC" : "\u2014";
}

function state(token: Token): "active" | "revoked" | "expired" {
  if (token.revoked_at) return "revoked";
  if (token.expires_at && new Date(token.expires_at) < new Date()) return "expired";
  return "active";
}

export function TokenPanel({
  orgSlug,
  clusterId,
  tokens,
  canManage,
}: {
  orgSlug: string;
  clusterId: string;
  tokens: Token[];
  canManage: boolean;
}) {
  const [created, setCreated] = useState<Extract<TokenResult, { ok: true }> | null>(null);
  const [confirming, setConfirming] = useState<string | null>(null);
  const [pending, startTransition] = useTransition();

  function onCreate() {
    startTransition(async () => {
      const result = await createToken(orgSlug, clusterId);
      if (result.ok) setCreated(result);
      else toast.error(result.error);
    });
  }

  function onRevoke(token: Token) {
    // Two clicks instead of a native dialog: the first arms the button, the second revokes.
    if (confirming !== token.id) {
      setConfirming(token.id);
      return;
    }
    setConfirming(null);
    startTransition(async () => {
      const result = await revokeToken(orgSlug, clusterId, token.id);
      if (result.ok) {
        if (created?.prefix === token.prefix) setCreated(null);
        toast.success("Token revoked. The agent using it will be disconnected.");
      } else toast.error(result.error);
    });
  }

  return (
    <section className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold">Agent tokens</h2>
        {canManage && (
          <Button onClick={onCreate} disabled={pending}>
            New token
          </Button>
        )}
      </div>
      {created && <InstallInstructions token={created.token} command={created.command} />}
      {tokens.length === 0 ? (
        <p className="text-sm text-muted-foreground">No tokens yet.</p>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Token</TableHead>
              <TableHead>Status</TableHead>
              <TableHead>Created</TableHead>
              <TableHead>Last used</TableHead>
              <TableHead />
            </TableRow>
          </TableHeader>
          <TableBody>
            {tokens.map((token) => {
              const tokenState = state(token);
              return (
                <TableRow key={token.id}>
                  <TableCell className="font-mono text-xs">{token.prefix}&hellip;</TableCell>
                  <TableCell>
                    <Badge variant={tokenState === "active" ? "default" : "secondary"}>{tokenState}</Badge>
                  </TableCell>
                  <TableCell>{formatDate(token.created_at)}</TableCell>
                  <TableCell>{formatDate(token.last_used_at)}</TableCell>
                  <TableCell className="text-right">
                    {canManage && tokenState === "active" && (
                      <Button
                        variant="destructive"
                        size="sm"
                        onClick={() => onRevoke(token)}
                        onBlur={() => setConfirming(null)}
                        disabled={pending}
                      >
                        {confirming === token.id ? "Click again to revoke" : "Revoke"}
                      </Button>
                    )}
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      )}
    </section>
  );
}
