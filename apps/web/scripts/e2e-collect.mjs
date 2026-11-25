// End-to-end collection test (task 2.6): panel sign-up -> cluster + token -> agent on
// kind (helm) -> audit -> masked snapshot in S3.
//
// Needs: web on WEB_URL, api on API_URL (listening on 0.0.0.0), a kind cluster with the
// agent image loaded, and AGENT_ENDPOINT reachable from inside kind.
// Usage: node scripts/e2e-collect.mjs
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { randomBytes } from "node:crypto";
import { gunzipSync } from "node:zlib";

const WEB = process.env.WEB_URL ?? "http://localhost:3000";
const API = process.env.API_URL ?? "http://localhost:8000";
const AGENT_ENDPOINT = process.env.AGENT_ENDPOINT;
const KUBE_CONTEXT = process.env.KUBE_CONTEXT ?? "kind-ape-dev";
const REPO = new URL("../../../", import.meta.url).pathname;
assert.ok(AGENT_ENDPOINT, "AGENT_ENDPOINT is required, e.g. ws://192.168.5.2:8000/v1/agent");

const PLANTED = [
  "e2e-annotation-token-9f8e7d",
  "MIIBe2eFAKEe2eFAKEe2eFAKEe2eFAKE",
  "e2e-arg-password-1a2b3c",
  "e2e-env-password-4d5e6f",
];

const run = randomBytes(4).toString("hex");
const cookies = new Map();
async function web(path, body) {
  const headers = { Origin: WEB, Cookie: [...cookies].map(([k, v]) => `${k}=${v}`).join("; ") };
  if (body) headers["Content-Type"] = "application/json";
  const res = await fetch(WEB + path, { method: body ? "POST" : "GET", headers, body: body && JSON.stringify(body) });
  for (const cookie of res.headers.getSetCookie()) {
    const [pair] = cookie.split(";");
    cookies.set(pair.slice(0, pair.indexOf("=")), pair.slice(pair.indexOf("=") + 1));
  }
  assert.ok(res.ok, `${path}: ${res.status}`);
  return res.json();
}
async function api(path, method = "GET", body) {
  const { token } = await web("/api/auth/token");
  const res = await fetch(API + path, {
    method,
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    body: body && JSON.stringify(body),
  });
  return { status: res.status, data: await res.json() };
}
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
async function until(what, fn, seconds = 120) {
  const deadline = Date.now() + seconds * 1000;
  for (;;) {
    const value = await fn();
    if (value) return value;
    if (Date.now() > deadline) throw new Error(`timed out waiting for ${what}`);
    await sleep(1000);
  }
}
const sh = (cmd, args) => execFileSync(cmd, args, { cwd: REPO, stdio: ["ignore", "pipe", "inherit"] }).toString();

// --- panel: account, workspace, cluster, token ----------------------------------
await web("/api/auth/sign-up/email", { name: "E2E", email: `e2e-${run}@smoke.test`, password: `E2e-${randomBytes(9).toString("base64url")}` });
const org = await web("/api/auth/organization/create", { name: `E2E ${run}`, slug: `e2e-${run}` });
await web("/api/auth/organization/set-active", { organizationId: org.id });
const cluster = (await api("/v1/clusters", "POST", { name: "kind" })).data;
const token = (await api(`/v1/clusters/${cluster.id}/tokens`, "POST")).data.token;
console.log("ok - cluster and agent token created through the API");

// --- data plane: sample workload and the agent chart -----------------------------
sh("kubectl", ["--context", KUBE_CONTEXT, "apply", "-f", "deploy/kind/sample-workload.yaml"]);
sh("kubectl", ["--context", KUBE_CONTEXT, "apply", "-f", "testdata/vulnerable/manifests/"]);
sh("helm", [
  "--kube-context", KUBE_CONTEXT, "upgrade", "--install", "ape-agent", "deploy/helm/ape-agent",
  "-n", "ape-system", "--create-namespace",
  "--set", `token=${token}`, "--set", `endpoint=${AGENT_ENDPOINT}`, "--set", "allowInsecure=true",
  "--set", "image.tag=dev", "--set", "networkPolicy.egressPorts={443,6443,8000}",
  "--wait", "--timeout", "120s",
]);
console.log("ok - agent installed with helm");

const connected = await until("agent to connect", async () => {
  const { data } = await api(`/v1/clusters/${cluster.id}`);
  return data.status === "connected" && data;
});
assert.match(connected.k8s_version, /^v1\./);
console.log(`ok - panel shows the cluster connected (Kubernetes ${connected.k8s_version})`);

// --- audit ----------------------------------------------------------------------
const started = await api(`/v1/clusters/${cluster.id}/audits`, "POST");
assert.equal(started.status, 201, JSON.stringify(started.data));
const audit = await until("collection and analysis to finish", async () => {
  const { data } = await api(`/v1/audits/${started.data.id}`);
  if (data.status === "failed") throw new Error(`audit failed: ${data.error}`);
  return data.status === "completed" && data;
}, 180);
assert.ok(audit.stats.objects > 20, `only ${audit.stats.objects} objects collected`);
assert.ok(audit.stats.counts.Pod > 0 && audit.stats.counts.ClusterRole > 0 && audit.stats.counts.Node > 0);
console.log(`ok - audit collected ${audit.stats.objects} objects in ${audit.stats.chunks} chunks, redactions: ${JSON.stringify(audit.stats.redactions)}`);

// --- the stored snapshot is masked and sits under the tenant prefix ---------------
const key = `${org.id}/${audit.id}/snapshot.json.gz`;
const py = `
import boto3, os, sys
from botocore.config import Config
c = boto3.client("s3", endpoint_url=os.environ["S3_ENDPOINT_URL"], aws_access_key_id=os.environ["S3_ACCESS_KEY"],
                 aws_secret_access_key=os.environ["S3_SECRET_KEY"], region_name="us-east-1", config=Config(s3={"addressing_style": "path"}))
sys.stdout.buffer.write(c.get_object(Bucket=os.environ.get("S3_BUCKET_SNAPSHOTS", "snapshots"), Key=sys.argv[1])["Body"].read())
`;
const gz = execFileSync("uv", ["run", "python", "-c", py, key], { cwd: `${REPO}apps/api`, maxBuffer: 256 * 1024 * 1024 });
const text = gunzipSync(gz).toString();
for (const secret of PLANTED) assert.ok(!text.includes(secret), `planted secret ${secret} is in the stored snapshot`);
assert.ok(!text.includes("managedFields"), "managedFields reached the control plane");
const snapshot = JSON.parse(text);
const leaky = snapshot.objects.find((o) => o.kind === "Deployment" && o.metadata.name === "leaky");
assert.ok(leaky, "sample deployment is missing from the snapshot");
const container = leaky.spec.template.spec.containers[0];
assert.match(container.env[0].value, /^<redacted:len=\d+>$/);
assert.match(container.args[0], /^--db-password=<redacted:len=\d+>$/);
assert.match(leaky.metadata.annotations["example.com/api-token"], /^<secret:key_name:[0-9a-f]{4}>$/);
assert.match(leaky.metadata.annotations["example.com/ca"], /<secret:pem:[0-9a-f]{4}>/);
assert.ok(!snapshot.objects.some((o) => o.kind === "Secret" || o.kind === "ConfigMap"));
console.log(`ok - snapshot ${key} holds ${snapshot.objects.length} masked objects and none of the planted secrets`);

// --- the worker analysed the snapshot: findings for the vulnerable set ----------------
const expectedYaml = execFileSync(
  "uv",
  ["run", "python", "-c", "import json,sys,yaml; print(json.dumps(yaml.safe_load(open(sys.argv[1]))))", `${REPO}testdata/vulnerable/expected.yaml`],
  { cwd: `${REPO}apps/api` },
).toString();
const expected = Object.entries(JSON.parse(expectedYaml)).flatMap(([rule, refs]) => refs.map((ref) => `${rule} ${ref}`)).sort();
const findings = (await api(`/v1/audits/${audit.id}/findings`)).data;
const inSet = (f) => /^vuln-/.test(f.resource_ns ?? "") || /^vuln-/.test(f.resource_name);
const actual = findings.filter(inSet).map((f) => `${f.rule_id} ${f.resource_kind}/${f.resource_ns ?? "-"}/${f.resource_name}`).sort();
assert.deepEqual(actual, expected);
assert.ok(!findings.some((f) => f.resource_ns === "clean-app" || f.resource_name === "clean-app"), "negative control namespace has findings");
assert.ok(findings.every((f) => f.explanation && f.remediation));
assert.equal(audit.stats.findings.total, findings.length);
console.log(`ok - worker produced ${findings.length} findings; the ${expected.length} expected ones for the vulnerable set match exactly, clean-app is clean`);

const planted = execFileSync("cat", [`${REPO}testdata/vulnerable/planted-secrets.txt`]).toString().split(/\s+/).filter(Boolean);
const everything = JSON.stringify(findings) + text;
for (const secret of planted) assert.ok(!everything.includes(secret), `planted secret ${secret} leaked`);
console.log("ok - none of the vulnerable set's planted secrets is in the snapshot or in the findings");

const page = await fetch(`${WEB}/e2e-${run}/audits/${audit.id}`, {
  headers: { Cookie: [...cookies].map(([k, v]) => `${k}=${v}`).join("; ") },
});
assert.equal(page.status, 200);
const html = await page.text();
assert.ok(html.includes("Privileged container") && html.includes("storefront") && html.includes("How to fix"));
console.log("ok - the audit page lists the findings with explanations");

// --- white-label report: score, sandboxed HTML preview, PDF download ---------------------
assert.ok(Number.isInteger(audit.score) && audit.score >= 0 && audit.score < 100, `score is ${audit.score}`);
assert.ok(html.includes('sandbox=""') && html.includes("Report preview") && html.includes("Download PDF"));
const reportRes = await fetch(`${API}/v1/audits/${audit.id}/report`, {
  headers: { Authorization: `Bearer ${(await web("/api/auth/token")).token}` },
});
assert.equal(reportRes.status, 200);
assert.match(reportRes.headers.get("content-security-policy"), /sandbox/);
const reportHtml = await reportRes.text();
assert.ok(reportHtml.includes("Kubernetes audit report") && reportHtml.includes("Executive summary"));
assert.ok(!/\bAPE\b|Autonomous Platform Engineering|ape-agent|ape-system/i.test(reportHtml), "product name in the report");
for (const secret of planted) assert.ok(!reportHtml.includes(secret), `planted secret ${secret} in the report`);
const pdfRedirect = await fetch(`${WEB}/e2e-${run}/audits/${audit.id}/report.pdf`, {
  headers: { Cookie: [...cookies].map(([k, v]) => `${k}=${v}`).join("; ") },
  redirect: "manual",
});
assert.equal(pdfRedirect.status, 307);
const pdf = Buffer.from(await (await fetch(pdfRedirect.headers.get("location"))).arrayBuffer());
assert.equal(pdf.subarray(0, 5).toString(), "%PDF-");
console.log(`ok - report built: score ${audit.score}, HTML preview is sandboxed, PDF downloads (${Math.round(pdf.length / 1024)} KB)`);

console.log("\ne2e collection test passed");
