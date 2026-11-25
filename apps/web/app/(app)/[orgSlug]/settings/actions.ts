"use server";

import { revalidatePath } from "next/cache";

import { apiFetch } from "@/lib/api";
import { requireOrganization } from "@/lib/session";

export type ActionResult = { ok: true } | { ok: false; error: string };

const MAX_LOGO_BYTES = 512 * 1024;

async function failure(res: Response): Promise<ActionResult> {
  const body = (await res.json().catch(() => null)) as { detail?: unknown } | null;
  if (res.status === 422) return { ok: false, error: "Check the company name and use a colour like #1f6feb." };
  return { ok: false, error: typeof body?.detail === "string" ? body.detail : `Request failed (${res.status}).` };
}

export async function saveBranding(orgSlug: string, form: FormData): Promise<ActionResult> {
  const { organization } = await requireOrganization(orgSlug);
  const companyName = String(form.get("company_name") ?? "").trim();
  const primaryColor = String(form.get("primary_color") ?? "").trim();
  const res = await apiFetch(organization.id, "/v1/settings/branding", {
    method: "PUT",
    body: JSON.stringify({ company_name: companyName || null, primary_color: primaryColor || null }),
  });
  if (!res.ok) return failure(res);

  const logo = form.get("logo");
  if (logo instanceof File && logo.size > 0) {
    if (logo.size > MAX_LOGO_BYTES) return { ok: false, error: "The logo must be smaller than 512 KB." };
    const upload = await apiFetch(organization.id, "/v1/settings/logo", {
      method: "PUT",
      headers: { "Content-Type": "application/octet-stream" },
      body: Buffer.from(await logo.arrayBuffer()),
    });
    if (!upload.ok) return failure(upload);
  }
  revalidatePath(`/${orgSlug}/settings/branding`);
  return { ok: true };
}

export async function removeLogo(orgSlug: string): Promise<ActionResult> {
  const { organization } = await requireOrganization(orgSlug);
  const res = await apiFetch(organization.id, "/v1/settings/logo", { method: "DELETE" });
  if (!res.ok) return failure(res);
  revalidatePath(`/${orgSlug}/settings/branding`);
  return { ok: true };
}

export async function chooseGitHubRepo(orgSlug: string, form: FormData): Promise<ActionResult> {
  const { organization } = await requireOrganization(orgSlug);
  const res = await apiFetch(organization.id, "/v1/settings/github/repo", {
    method: "PUT",
    body: JSON.stringify({
      repo: String(form.get("repo") ?? ""),
      manifest_path: String(form.get("manifest_path") ?? "/"),
    }),
  });
  if (!res.ok) {
    if (res.status === 422) return { ok: false, error: "Check the repository and the manifest directory." };
    return failure(res);
  }
  revalidatePath(`/${orgSlug}/settings/github`);
  return { ok: true };
}

export async function disconnectGitHub(orgSlug: string): Promise<ActionResult> {
  const { organization } = await requireOrganization(orgSlug);
  const res = await apiFetch(organization.id, "/v1/settings/github", { method: "DELETE" });
  if (!res.ok) return failure(res);
  revalidatePath(`/${orgSlug}/settings/github`);
  return { ok: true };
}
