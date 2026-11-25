"use client";

import { useRouter } from "next/navigation";
import { useEffect, useTransition } from "react";
import { toast } from "sonner";

import { startAudit } from "@/app/(app)/[orgSlug]/clusters/actions";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";

type Run = {
  id: string;
  status: string;
  objects: number | null;
  redactions: number | null;
  truncated: boolean;
  error: string | null;
  created_at: string;
};

const IN_PROGRESS = new Set(["queued", "collecting", "collected", "analyzing", "reporting"]);
const REFRESH_MS = 3000;

function formatDate(value: string): string {
  return new Date(value).toISOString().slice(0, 16).replace("T", " ") + " UTC";
}

export function AuditPanel({
  orgSlug,
  clusterId,
  runs,
  connected,
  canManage,
}: {
  orgSlug: string;
  clusterId: string;
  runs: Run[];
  connected: boolean;
  canManage: boolean;
}) {
  const router = useRouter();
  const [pending, startTransition] = useTransition();
  const collecting = runs.some((run) => run.status === "queued" || run.status === "collecting");
  const active = runs.some((run) => IN_PROGRESS.has(run.status) && run.status !== "collected");

  // Poll while something is moving; the page is otherwise static.
  useEffect(() => {
    if (!active) return;
    const timer = setInterval(() => router.refresh(), REFRESH_MS);
    return () => clearInterval(timer);
  }, [active, router]);

  function onStart() {
    startTransition(async () => {
      const result = await startAudit(orgSlug, clusterId);
      if (result.ok) toast.success("Audit started. The agent is collecting.");
      else toast.error(result.error);
    });
  }

  return (
    <section className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold">Audits</h2>
        {canManage && (
          <Button onClick={onStart} disabled={pending || !connected || collecting}>
            {collecting ? "Collecting\u2026" : "Start audit"}
          </Button>
        )}
      </div>
      {!connected && (
        <p className="text-sm text-muted-foreground">
          The agent is not connected. Install it with a token below to run an audit.
        </p>
      )}
      {runs.length > 0 && (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Started</TableHead>
              <TableHead>Status</TableHead>
              <TableHead>Objects</TableHead>
              <TableHead>Masked values</TableHead>
              <TableHead>Note</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {runs.map((run) => (
              <TableRow key={run.id}>
                <TableCell>{formatDate(run.created_at)}</TableCell>
                <TableCell>
                  <Badge variant={run.status === "failed" ? "destructive" : "secondary"}>{run.status}</Badge>
                </TableCell>
                <TableCell>{run.objects ?? "\u2014"}</TableCell>
                <TableCell>{run.redactions ?? "\u2014"}</TableCell>
                <TableCell className="max-w-xs truncate text-sm text-muted-foreground">
                  {run.error ?? (run.truncated ? "Truncated: a collection limit was reached" : "")}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </section>
  );
}
