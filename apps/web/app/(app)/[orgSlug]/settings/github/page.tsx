import { CopyButton } from "@/components/copy-button";
import { GitHubRepoForm } from "@/components/github-settings";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { buttonVariants } from "@/components/ui/button";
import { getGitHubStatus, listGitHubRepos } from "@/lib/settings";
import { requireOrganization } from "@/lib/session";

const ERRORS: Record<string, string> = {
  "403": "The GitHub account you signed in with cannot push to any repository of that installation.",
  "404":
    "The app is not installed on a repository your GitHub account can push to. Ask an owner of the GitHub " +
    "organisation to install it with the link below, then connect again.",
};

/** `id:account` pairs from the callback. Both parts are checked before anything is shown. */
function parseChoices(raw: unknown): { id: number; account: string }[] {
  if (typeof raw !== "string") return [];
  return raw
    .split(",")
    .slice(0, 20)
    .map((item) => {
      const [id, account = ""] = item.split(":");
      return { id: Number(id), account };
    })
    .filter((c) => Number.isSafeInteger(c.id) && c.id > 0 && /^[A-Za-z0-9-]{1,39}$/.test(c.account));
}

export default async function Page({ params, searchParams }: PageProps<"/[orgSlug]/settings/github">) {
  const { orgSlug } = await params;
  const { error, choose } = await searchParams;
  const { organization } = await requireOrganization(orgSlug);
  const status = await getGitHubStatus(organization.id);
  const repos = status.connected && status.can_manage ? await listGitHubRepos(organization.id) : [];
  const choices =
    !status.connected && status.can_manage
      ? await Promise.all(
          parseChoices(choose).map(async (c) => ({
            ...c,
            url: (await getGitHubStatus(organization.id, c.id)).connect_url,
          })),
        )
      : [];

  return (
    <section className="flex flex-col gap-4">
      <div>
        <h1 className="text-xl font-semibold">GitHub</h1>
        <p className="text-sm text-muted-foreground">
          Fixes are proposed as a pull request in the customer&apos;s repository. Nothing is ever applied to the
          cluster, and a pull request is only opened when you ask for it. Without a connection you can still
          download the fixes of an audit as files.
        </p>
      </div>
      {typeof error === "string" && (
        <Alert variant="destructive">
          <AlertTitle>GitHub could not be connected</AlertTitle>
          <AlertDescription>
            {ERRORS[error] ?? "The sign-in expired or was interrupted. Please try again."}
          </AlertDescription>
        </Alert>
      )}
      {!status.configured ? (
        <Alert>
          <AlertTitle>Not available yet</AlertTitle>
          <AlertDescription>The GitHub App has not been configured for this installation.</AlertDescription>
        </Alert>
      ) : !status.connected ? (
        status.can_manage && status.connect_url ? (
          <div className="flex max-w-2xl flex-col gap-6">
            {choices.length > 0 && (
              <div className="flex flex-col gap-2" data-testid="github-choices">
                <h2 className="text-sm font-medium">Your GitHub account can reach several accounts. Which one?</h2>
                <div className="flex flex-wrap gap-2">
                  {choices.map(
                    (c) =>
                      c.url && (
                        <a key={c.id} href={c.url} className={buttonVariants({ variant: "outline" })}>
                          {c.account}
                        </a>
                      ),
                  )}
                </div>
              </div>
            )}
            <div className="flex flex-col gap-2">
              <h2 className="text-sm font-medium">1. Get the app installed on the customer&apos;s GitHub account</h2>
              <p className="text-sm text-muted-foreground">
                GitHub only lets an owner of an organisation install an app. Send this link to an owner; they need
                no account here, and installing it gives this workspace nothing by itself.
              </p>
              {status.owner_install_url && (
                <div className="flex items-center gap-2">
                  <code className="min-w-0 flex-1 truncate rounded-lg border bg-muted px-2 py-1.5 text-xs">
                    {status.owner_install_url}
                  </code>
                  <CopyButton value={status.owner_install_url} label="Copy link" />
                </div>
              )}
              {status.install_url && (
                <p className="text-sm text-muted-foreground">
                  If you are an owner yourself,{" "}
                  <a href={status.install_url} className="font-medium underline">
                    install it now
                  </a>
                  ; that also connects it.
                </p>
              )}
            </div>
            <div className="flex flex-col gap-2">
              <h2 className="text-sm font-medium">2. Connect it with your own GitHub account</h2>
              <p className="text-sm text-muted-foreground">
                You sign in to GitHub, and this workspace is limited to the repositories you can push to. Your
                GitHub sign-in is not stored.
              </p>
              <a href={status.connect_url} className={buttonVariants({ className: "self-start" })}>
                Connect GitHub
              </a>
            </div>
          </div>
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
