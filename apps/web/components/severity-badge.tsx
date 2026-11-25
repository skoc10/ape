import { Badge } from "@/components/ui/badge";

const STYLE: Record<string, string> = {
  critical: "bg-red-600 text-white",
  high: "bg-orange-500 text-white",
  medium: "bg-amber-400 text-black",
  low: "bg-sky-200 text-sky-950",
  info: "bg-muted text-muted-foreground",
};

export function SeverityBadge({ severity }: { severity: string }) {
  return <Badge className={STYLE[severity] ?? STYLE.info}>{severity}</Badge>;
}
