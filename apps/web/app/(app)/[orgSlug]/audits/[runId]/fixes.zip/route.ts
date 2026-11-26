import { NextResponse } from "next/server";

import { apiFetch } from "@/lib/api";
import { isUuid } from "@/lib/clusters";
import { requireOrganization } from "@/lib/session";

/** Checks the session and membership, then passes the archive of patch files through. */
export async function GET(_request: Request, context: RouteContext<"/[orgSlug]/audits/[runId]/fixes.zip">) {
  const { orgSlug, runId } = await context.params;
  const { organization } = await requireOrganization(orgSlug);
  if (!isUuid(runId)) return new NextResponse("Not found", { status: 404 });
  const upstream = await apiFetch(organization.id, `/v1/audits/${runId}/fixes`);
  if (!upstream.ok) return new NextResponse("Fixes not available", { status: 404 });
  return new NextResponse(upstream.body, {
    status: 200,
    headers: {
      "Content-Type": "application/zip",
      "Content-Disposition": `attachment; filename="audit-fixes-${runId}.zip"`,
      "X-Content-Type-Options": "nosniff",
      "Cache-Control": "private, no-store",
    },
  });
}
