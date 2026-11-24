/** Top-level routes that an organization slug must not shadow. */
export const RESERVED_SLUGS: ReadonlySet<string> = new Set([
  "api",
  "sign-in",
  "sign-up",
  "onboarding",
  "accept-invitation",
  "_next",
]);

const SLUG = /^[a-z0-9]([a-z0-9-]{0,46}[a-z0-9])?$/;

export function isValidOrgSlug(slug: string): boolean {
  return SLUG.test(slug) && !RESERVED_SLUGS.has(slug);
}
