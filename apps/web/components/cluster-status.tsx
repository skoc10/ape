import { Badge } from "@/components/ui/badge";

const VARIANT = { connected: "default", pending: "secondary", offline: "destructive" } as const;

export function ClusterStatus({ status }: { status: "pending" | "connected" | "offline" }) {
  return <Badge variant={VARIANT[status]}>{status}</Badge>;
}
