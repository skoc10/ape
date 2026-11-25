import { NextResponse } from "next/server";

import { getReportPdfUrl } from "@/lib/clusters";
import { requireOrganization } from "@/lib/session";

/** Checks the session and membership, then redirects to a short-lived download link. */
export async function GET(_request: Request, context: RouteContext<"/[orgSlug]/audits/[runId]/report.pdf">) {
  const { orgSlug, runId } = await context.params;
  const { organization } = await requireOrganization(orgSlug);
  const url = await getReportPdfUrl(organization.id, runId);
  if (!url) return new NextResponse("Report not available", { status: 404 });
  return NextResponse.redirect(url, { status: 307, headers: { "Cache-Control": "no-store" } });
}
