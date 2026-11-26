import Link from "next/link";
import { notFound } from "next/navigation";

import { AutoRefresh } from "@/components/auto-refresh";
import { FindingFilters } from "@/components/finding-filters";
import { PullRequestButton } from "@/components/pull-request-button";
import { SeverityBadge } from "@/components/severity-badge";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  CATEGORIES,
  SEVERITIES,
  getAudit,
  getReportHtml,
  listFindings,
  type Category,
  type Severity,
} from "@/lib/clusters";
import { requireOrganization } from "@/lib/session";

const IN_PROGRESS = new Set(["queued", "collecting", "collected", "analyzing", "reporting"]);

const STAGE: Record<string, string> = {
  queued: "Waiting for the agent",
  collecting: "The agent is collecting the cluster inventory",
  collected: "Collected and masked; waiting for analysis",
  analyzing: "Running the rules and writing explanations",
  reporting: "Preparing the report",
  completed: "Completed",
  failed: "Failed",
};

function pick<T extends string>(value: string | string[] | undefined, allowed: readonly T[]): T | undefined {
  return typeof value === "string" && (allowed as readonly string[]).includes(value) ? (value as T) : undefined;
}

export default async function Page({ params, searchParams }: PageProps<"/[orgSlug]/audits/[runId]">) {
  const { orgSlug, runId } = await params;
  const query = await searchParams;
  const { organization } = await requireOrganization(orgSlug);
  const audit = await getAudit(organization.id, runId);
  if (!audit) notFound();

  const severity = pick<Severity>(query.severity, SEVERITIES);
  const category = pick<Category>(query.category, CATEGORIES);
  const findings = await listFindings(organization.id, audit.id, { severity, category });
  const reportHtml = audit.status === "completed" ? await getReportHtml(organization.id, audit.id) : null;
  const counts = audit.stats.findings;
  const active = IN_PROGRESS.has(audit.status);

  return (
    <div className="flex flex-col gap-6">
      <AutoRefresh active={active} />
      <section className="flex flex-col gap-2">
        <Link href={`/${orgSlug}/clusters/${audit.cluster_id}`} className="text-sm text-muted-foreground hover:underline">
          &larr; Cluster
        </Link>
        <div className="flex flex-wrap items-center gap-3">
          <h1 className="text-xl font-semibold">Audit</h1>
          <Badge variant={audit.status === "failed" ? "destructive" : "secondary"} data-testid="audit-status">
            {audit.status}
          </Badge>
          <span className="text-sm text-muted-foreground">{STAGE[audit.status] ?? audit.status}</span>
        </div>
        <p className="text-sm text-muted-foreground">
          Started {new Date(audit.created_at).toISOString().slice(0, 16).replace("T", " ")} UTC
          {audit.stats.objects !== undefined && <> &middot; {audit.stats.objects} objects collected</>}
          {audit.stats.llm && (
            <>
              {" "}
              &middot;{" "}
              {audit.stats.llm.enabled
                ? `explanations by ${audit.stats.llm.model}`
                : "template explanations (no LLM configured)"}
            </>
          )}
        </p>
        {audit.error && <p className="text-sm text-destructive">{audit.error}</p>}
      </section>

      {counts && (
        <Card>
          <CardHeader>
            <CardTitle>
              {audit.score !== null && <span data-testid="audit-score">Score {audit.score} / 100 &middot; </span>}
              {counts.total} findings
            </CardTitle>
            <CardDescription>{audit.stats.summary}</CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col gap-4">
            <div className="flex flex-wrap gap-2">
              {SEVERITIES.filter((s) => counts[s]).map((s) => (
                <span key={s} className="flex items-center gap-1 text-sm">
                  <SeverityBadge severity={s} /> {counts[s]}
                </span>
              ))}
            </div>
            {audit.stats.top_risks && audit.stats.top_risks.length > 0 && (
              <div>
                <h2 className="mb-1 text-sm font-medium">Top risks</h2>
                <ol className="list-decimal pl-5 text-sm text-muted-foreground">
                  {audit.stats.top_risks.map((risk) => (
                    <li key={risk}>{risk}</li>
                  ))}
                </ol>
              </div>
            )}
          </CardContent>
        </Card>
      )}

      {audit.status === "completed" && (audit.pr_url || findings.some((f) => f.patch)) && (
        <section className="flex flex-wrap items-center gap-3">
          {findings.some((f) => f.patch) && (
            // Works without any repository connection: the patches as files, to apply with
            // the access the reader already has.
            <a
              href={`/${orgSlug}/audits/${audit.id}/fixes.zip`}
              data-testid="download-fixes"
              className="rounded-lg border px-3 py-1.5 text-sm font-medium hover:bg-muted"
            >
              Download fixes
            </a>
          )}
          {audit.pr_url ? (
            // The URL comes from GitHub through our API; only https links are rendered.
            audit.pr_url.startsWith("https://") && (
              <a href={audit.pr_url} target="_blank" rel="noreferrer noopener" className="text-sm font-medium underline">
                View the pull request
              </a>
            )
          ) : (
            <>
              <PullRequestButton orgSlug={orgSlug} runId={audit.id} />
              <span className="text-sm text-muted-foreground">
                Download the patches as files, or propose them in the connected repository. Nothing is applied
                to the cluster.
              </span>
            </>
          )}
        </section>
      )}

      {reportHtml && (
        <section className="flex flex-col gap-3">
          <div className="flex items-center justify-between">
            <h2 className="text-lg font-semibold">Report</h2>
            {audit.has_report && (
              <a
                href={`/${orgSlug}/audits/${audit.id}/report.pdf`}
                className="rounded-lg bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground"
              >
                Download PDF
              </a>
            )}
          </div>
          {/* sandbox="" : no scripts, no forms, no navigation, an opaque origin. The report text
              comes from cluster data and an LLM, so it is displayed as an inert document. */}
          <iframe
            title="Report preview"
            sandbox=""
            srcDoc={reportHtml}
            className="h-[640px] w-full rounded-lg border bg-white"
          />
        </section>
      )}

      {audit.status === "completed" && (
        <section className="flex flex-col gap-4">
          <FindingFilters severities={SEVERITIES} categories={CATEGORIES} />
          {findings.length === 0 && <p className="text-sm text-muted-foreground">No findings match the filters.</p>}
          {findings.map((finding) => (
            <Card key={finding.id} data-testid="finding">
              <CardHeader>
                <div className="flex flex-wrap items-center gap-2">
                  <SeverityBadge severity={finding.severity} />
                  <Badge variant="outline">{finding.category}</Badge>
                  <Badge variant="outline" className="font-mono">
                    {finding.rule_id}
                  </Badge>
                  {finding.recurring && <Badge variant="secondary">recurring</Badge>}
                  {typeof finding.evidence?.platformComponent === "string" && (
                    <Badge variant="outline">{finding.evidence.platformComponent}</Badge>
                  )}
                </div>
                <CardTitle className="mt-2">{finding.title}</CardTitle>
                <CardDescription className="font-mono text-xs">
                  {finding.resource_kind} {finding.resource_ns ? `${finding.resource_ns}/` : ""}
                  {finding.resource_name}
                </CardDescription>
              </CardHeader>
              <CardContent className="flex flex-col gap-3 text-sm">
                {finding.explanation && <p className="whitespace-pre-line">{finding.explanation}</p>}
                {finding.remediation && (
                  <div>
                    <h3 className="font-medium">How to fix</h3>
                    <p className="whitespace-pre-line text-muted-foreground">{finding.remediation}</p>
                  </div>
                )}
                {finding.patch && (
                  <div>
                    <h3 className="font-medium">Proposed patch</h3>
                    <pre className="overflow-x-auto rounded-lg border bg-muted px-3 py-2 font-mono text-xs">
                      {finding.patch}
                    </pre>
                  </div>
                )}
              </CardContent>
            </Card>
          ))}
        </section>
      )}
    </div>
  );
}
