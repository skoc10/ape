const INTERNAL = "http://internal.invalid";

/**
 * Accepts only same-site relative paths, so `?next=` cannot be an open redirect.
 *
 * Browsers strip tabs and newlines from URLs, so "/\t/evil.example" would become
 * "//evil.example": control characters and backslashes are rejected outright,
 * and the result must still resolve to our own origin.
 */
export function safeNextPath(next: string | string[] | undefined, fallback = "/"): string {
  if (typeof next !== "string") return fallback;
  if (!next.startsWith("/") || next.startsWith("//")) return fallback;
  if (/[\\\u0000-\u0020\u007f]/.test(next)) return fallback;
  try {
    if (new URL(next, INTERNAL).origin !== INTERNAL) return fallback;
  } catch {
    return fallback;
  }
  return next;
}
