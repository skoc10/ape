/** Shown to whoever installed the GitHub App from the plain link. Needs no account. */
export default function Page() {
  return (
    <main className="flex flex-1 items-center justify-center bg-muted/40 p-6">
      <div className="max-w-md rounded-xl border bg-card p-6 text-card-foreground">
        <h1 className="text-lg font-semibold">The app is installed</h1>
        <p className="mt-2 text-sm text-muted-foreground">
          You can close this page. The engineer who sent you the link will finish the connection with their own
          GitHub account, and can only use the repositories they are able to push to.
        </p>
        <p className="mt-2 text-sm text-muted-foreground">
          The app proposes changes as pull requests. It never merges anything, and you can remove it at any time
          in your GitHub settings under Applications.
        </p>
      </div>
    </main>
  );
}
