import { CopyButton } from "@/components/copy-button";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";

/** Shows a freshly created agent token and the helm command. Rendered once; the token cannot be fetched again. */
export function InstallInstructions({ token, command }: { token: string; command: string }) {
  return (
    <div className="flex flex-col gap-4">
      <Alert>
        <AlertTitle>Copy the token now</AlertTitle>
        <AlertDescription>
          This is the only time it is shown. We store just a hash, so a lost token has to be revoked and replaced.
        </AlertDescription>
      </Alert>
      <div className="flex flex-col gap-2">
        <div className="flex items-center justify-between">
          <span className="text-sm font-medium">Agent token</span>
          <CopyButton value={token} label="Copy token" />
        </div>
        <code data-testid="agent-token" className="rounded-lg border bg-muted px-3 py-2 font-mono text-xs break-all">
          {token}
        </code>
      </div>
      <div className="flex flex-col gap-2">
        <div className="flex items-center justify-between">
          <span className="text-sm font-medium">Install the read-only agent in the customer cluster</span>
          <CopyButton value={command} label="Copy command" />
        </div>
        <pre
          data-testid="helm-command"
          className="overflow-x-auto rounded-lg border bg-muted px-3 py-2 font-mono text-xs leading-relaxed"
        >
          {command}
        </pre>
        <p className="text-xs text-muted-foreground">
          The agent only needs outbound access on port 443. It cannot write to the cluster and never reads Secret
          or ConfigMap contents.
        </p>
      </div>
    </div>
  );
}
