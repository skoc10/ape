import assert from "node:assert/strict";
import { test } from "node:test";

import { safeNextPath } from "./safe-redirect.ts";
import { isValidOrgSlug } from "./slug.ts";

test("same-site paths are kept", () => {
  for (const path of ["/", "/acme/clusters", "/accept-invitation/abc123", "/a?b=c#d"]) {
    assert.equal(safeNextPath(path), path);
  }
});

test("anything that can leave the site falls back", () => {
  const hostile = [
    "//evil.example",
    "/\t/evil.example",
    "/\n/evil.example",
    "/\r/evil.example",
    "/\\evil.example",
    "\\\\evil.example",
    "/ /evil.example",
    "/\u0000/evil.example",
    "https://evil.example",
    "javascript:alert(1)",
    "evil.example",
    "",
  ];
  for (const value of hostile) {
    assert.equal(safeNextPath(value), "/", JSON.stringify(value));
  }
  assert.equal(safeNextPath(undefined), "/");
  assert.equal(safeNextPath(["/a", "/b"]), "/");
  assert.equal(safeNextPath("//evil.example", "/home"), "/home");
});

test("organization slugs cannot shadow application routes", () => {
  assert.ok(isValidOrgSlug("acme-corp"));
  for (const slug of ["sign-in", "api", "onboarding", "_next", "Acme", "-acme", "a/b", ""]) {
    assert.equal(isValidOrgSlug(slug), false, slug);
  }
});
