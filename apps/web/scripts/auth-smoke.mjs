// End-to-end smoke test for task 1.3: sign-up, organizations, switching,
// invitations, the API JWT and the JWKS endpoint. Needs a running web server
// and a migrated database. Usage: node scripts/auth-smoke.mjs [baseUrl]
import assert from "node:assert/strict";
import { randomBytes } from "node:crypto";

import { createRemoteJWKSet, jwtVerify } from "jose";

const BASE = process.argv[2] ?? process.env.BETTER_AUTH_URL ?? "http://localhost:3000";
const run = randomBytes(4).toString("hex");

class Client {
  cookies = new Map();

  async request(path, { method = "GET", body } = {}) {
    const headers = { Origin: BASE };
    if (this.cookies.size) {
      headers.Cookie = [...this.cookies].map(([k, v]) => `${k}=${v}`).join("; ");
    }
    if (body !== undefined) headers["Content-Type"] = "application/json";
    const res = await fetch(BASE + path, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      redirect: "manual",
    });
    for (const cookie of res.headers.getSetCookie()) {
      const [pair] = cookie.split(";");
      const index = pair.indexOf("=");
      this.cookies.set(pair.slice(0, index), pair.slice(index + 1));
    }
    return res;
  }

  async json(path, options) {
    const res = await this.request(path, options);
    const text = await res.text();
    return { status: res.status, data: text ? JSON.parse(text) : null };
  }
}

function ok(name) {
  console.log(`ok - ${name}`);
}

const password = `Smoke-${randomBytes(9).toString("base64url")}`;
const alice = new Client();
const bob = new Client();
const anonymous = new Client();
const aliceEmail = `alice-${run}@smoke.test`;
const bobEmail = `bob-${run}@smoke.test`;

// --- unauthenticated ---------------------------------------------------------
let res = await anonymous.request("/");
assert.equal(res.status, 307);
assert.match(res.headers.get("location"), /\/sign-in/);
ok("anonymous visitor is redirected to /sign-in");
assert.equal((await anonymous.request("/sign-in")).status, 200);
ok("/sign-in renders");
assert.equal((await anonymous.json("/api/auth/token")).status, 401);
ok("no API token without a session");

// --- sign up and sign in -----------------------------------------------------
let r = await alice.json("/api/auth/sign-up/email", {
  method: "POST",
  body: { name: "Alice", email: aliceEmail, password },
});
assert.equal(r.status, 200, JSON.stringify(r.data));
const aliceId = r.data.user.id;
ok("sign up with email and password");

r = await new Client().json("/api/auth/sign-up/email", {
  method: "POST",
  body: { name: "Weak", email: `weak-${run}@smoke.test`, password: "short" },
});
assert.equal(r.status, 400);
ok("short passwords are rejected");

const aliceAgain = new Client();
r = await aliceAgain.json("/api/auth/sign-in/email", { method: "POST", body: { email: aliceEmail, password } });
assert.equal(r.status, 200);
ok("sign in with email and password");
r = await new Client().json("/api/auth/sign-in/email", {
  method: "POST",
  body: { email: aliceEmail, password: `${password}x` },
});
assert.equal(r.status, 401);
ok("wrong password is rejected");

// --- organizations -----------------------------------------------------------
r = await alice.json("/api/auth/organization/create", {
  method: "POST",
  body: { name: `Customer One ${run}`, slug: `customer-one-${run}` },
});
assert.equal(r.status, 200, JSON.stringify(r.data));
const org1 = r.data;
r = await alice.json("/api/auth/organization/create", {
  method: "POST",
  body: { name: `Customer Two ${run}`, slug: `customer-two-${run}` },
});
assert.equal(r.status, 200);
const org2 = r.data;
ok("create two organizations");

// --- JWT and JWKS ------------------------------------------------------------
const jwks = createRemoteJWKSet(new URL("/api/auth/jwks", BASE));
async function apiToken(client) {
  const { status, data } = await client.json("/api/auth/token");
  assert.equal(status, 200);
  const { payload } = await jwtVerify(data.token, jwks, { issuer: BASE, audience: "ape-api" });
  return payload;
}

r = await alice.json("/api/auth/organization/set-active", { method: "POST", body: { organizationId: org1.id } });
assert.equal(r.status, 200);
let claims = await apiToken(alice);
assert.equal(claims.sub, aliceId);
assert.equal(claims.org_id, org1.id);
assert.equal(claims.exp - claims.iat, 15 * 60);
assert.equal(claims.email, undefined);
ok("JWT verifies against /api/auth/jwks and carries sub, org_id and a 15 minute expiry");

r = await alice.json("/api/auth/organization/set-active", { method: "POST", body: { organizationId: org2.id } });
assert.equal(r.status, 200);
claims = await apiToken(alice);
assert.equal(claims.org_id, org2.id);
ok("switching the active organization changes org_id in the next token");

res = await anonymous.request("/api/auth/jwks");
const keys = (await res.json()).keys;
assert.ok(keys.length >= 1);
assert.ok(keys.every((k) => k.d === undefined));
ok("/api/auth/jwks is public and exposes no private key material");

// --- invitations and membership boundaries ------------------------------------
r = await bob.json("/api/auth/sign-up/email", { method: "POST", body: { name: "Bob", email: bobEmail, password } });
assert.equal(r.status, 200);

r = await bob.json("/api/auth/organization/set-active", { method: "POST", body: { organizationId: org1.id } });
assert.ok(r.status >= 400);
ok("a non-member cannot activate someone else's organization");
res = await bob.request(`/${org1.slug}/clusters`);
assert.equal(res.status, 404);
ok("a non-member gets 404 on another organization's pages");

r = await alice.json("/api/auth/organization/invite-member", {
  method: "POST",
  body: { organizationId: org1.id, email: bobEmail, role: "member" },
});
assert.equal(r.status, 200, JSON.stringify(r.data));
const invitationId = r.data.id;
ok("owner invites a member");

r = await bob.json("/api/auth/organization/invite-member", {
  method: "POST",
  body: { organizationId: org1.id, email: `eve-${run}@smoke.test`, role: "member" },
});
assert.ok(r.status >= 400);
ok("a non-member cannot invite");

res = await bob.request(`/accept-invitation/${invitationId}`);
assert.equal(res.status, 200);
r = await bob.json("/api/auth/organization/accept-invitation", { method: "POST", body: { invitationId } });
assert.equal(r.status, 200, JSON.stringify(r.data));
ok("invited user accepts the invitation");

r = await bob.json("/api/auth/organization/set-active", { method: "POST", body: { organizationId: org1.id } });
assert.equal(r.status, 200);
claims = await apiToken(bob);
assert.equal(claims.org_id, org1.id);
res = await bob.request(`/${org1.slug}/clusters`);
assert.equal(res.status, 200);
ok("the new member can open the organization and gets a token for it");

r = await bob.json("/api/auth/organization/set-active", { method: "POST", body: { organizationId: org2.id } });
assert.ok(r.status >= 400);
res = await bob.request(`/${org2.slug}/clusters`);
assert.equal(res.status, 404);
ok("membership in one organization gives no access to another");

res = await alice.request(`/${org1.slug}/settings/members`);
assert.equal(res.status, 200);
ok("members page renders for the owner");

console.log("\nauth smoke test passed");
