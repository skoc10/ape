/**
 * An absolute URL of this panel for a redirect.
 *
 * Behind an ingress the request URL carries the address the server listens on
 * (for example https://0.0.0.0:3000), not the one the browser used. Redirects built from
 * it send people to an address that does not exist for them, so the configured public
 * address wins and the request URL is only the fallback for local development.
 */
export function publicUrl(path: string, requestUrl: string, base = process.env.BETTER_AUTH_URL): URL {
  return new URL(path, base || requestUrl);
}
