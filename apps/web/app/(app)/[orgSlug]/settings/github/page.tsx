import { GitHubRepoForm } from "@/components/github-settings";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { buttonVariants } from "@/components/ui/button";
import { getGitHubStatus, listGitHubRepos } from "@/lib/settings";
import { requireOrganization } from "@/lib/session";

export default async function Page({ params, searchParams }: PageProps<"/[orgSlug]/settings/github">) {
  const { orgSlug } = await params;
  const { error } = await searchParams;
  const { organization } = await requireOrganization(orgSlug);
  const status = await getGitHubStatus(organization.id);
  const repos = status.connected && status.can_manage ? await listGitHubRepos(organization.id) : [];

  return (
    <section className="flex flex-col gap-4">
      <div>
        <h1 className="text-xl font-semibold">GitHub</h1>
        <p className="text-sm text-muted-foreground">
          Fixes are proposed as a pull request in the customer&apos;s repository. Nothing is ever applied to the
          cluster, and a pull request is only opened when you ask for it.
        </p>
      </div>
      {typeof error === "string" && (
        <Alert variant="destructive">
          <AlertTitle>GitHub could not be connected</AlertTitle>
          <AlertDescription>
            {error === "403"
              ? "The GitHub account you signed in with does not control that installation."
              : "The sign-in expired or was interrupted. Please try again."}
          </AlertDescription>
        </Alert>
      )}
      {!status.configured ? (
        <Alert>
          <AlertTitle>Not available yet</AlertTitle>
          <AlertDescription>The GitHub App has not been configured for this installation.</AlertDescription>
        </Alert>
      ) : !status.connected ? (
        status.install_url ? (
          <a href={status.install_url} className={buttonVariants({ className: "self-start" })}>
            Install the GitHub App
          </a>
        ) : (
          <p className="text-sm text-muted-foreground">An owner or admin has to connect GitHub.</p>
        )
      ) : status.can_manage ? (
        <GitHubRepoForm orgSlug={orgSlug} repos={repos} repo={status.repo} manifestPath={status.manifest_path ?? "/"} />
      ) : (
        <p className="text-sm">
          Connected to <span className="font-mono">{status.repo ?? "no repository yet"}</span>.
        </p>
      )}
    </section>
  );
}
