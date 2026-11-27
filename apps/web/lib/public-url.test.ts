import assert from "node:assert/strict";
import { test } from "node:test";

import { publicUrl } from "./public-url.ts";

test("redirects use the public address, not the one the server listens on", () => {
  const internal = "https://0.0.0.0:3000/github/callback?code=x";
  assert.equal(publicUrl("/github/installed", internal, "https://cp.example.com").href, "https://cp.example.com/github/installed");
  assert.equal(publicUrl("/acme/settings/github", internal, "https://cp.example.com/").href, "https://cp.example.com/acme/settings/github");
});

test("without a configured address the request URL is the fallback", () => {
  assert.equal(publicUrl("/", "http://localhost:3000/github/callback", "").href, "http://localhost:3000/");
  assert.equal(publicUrl("/", "http://localhost:3000/github/callback", undefined).origin, process.env.BETTER_AUTH_URL ? new URL(process.env.BETTER_AUTH_URL).origin : "http://localhost:3000");
});
