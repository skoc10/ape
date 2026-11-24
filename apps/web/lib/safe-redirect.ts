/** Accepts only same-site relative paths, so `?next=` cannot be an open redirect. */
export function safeNextPath(next: string | string[] | undefined, fallback = "/"): string {
  if (typeof next !== "string") return fallback;
  if (!next.startsWith("/") || next.startsWith("//") || next.includes("\\")) return fallback;
  return next;
}
