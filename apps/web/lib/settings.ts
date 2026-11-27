import "server-only";

import { apiFetch } from "./api";

export type WorkspaceSettings = {
  branding: { company_name: string | null; primary_color: string | null; has_logo: boolean };
  github_installation_id: number | null;
  github_repo: string | null;
  manifest_path: string | null;
  can_manage: boolean;
};

export async function getSettings(orgId: string): Promise<WorkspaceSettings> {
  const res = await apiFetch(orgId, "/v1/settings");
  if (!res.ok) throw new Error(`API error ${res.status}`);
  return res.json();
}

/** The logo as a data URI for the settings preview, or null when there is none. */
export async function getLogoDataUri(orgId: string): Promise<string | null> {
  const res = await apiFetch(orgId, "/v1/settings/logo");
  if (!res.ok) return null;
  const type = res.headers.get("content-type");
  if (type !== "image/png" && type !== "image/jpeg") return null;
  return `data:${type};base64,${Buffer.from(await res.arrayBuffer()).toString("base64")}`;
}

export type GitHubStatus = {
  configured: boolean;
  connected: boolean;
  repo: string | null;
  manifest_path: string | null;
  install_url: string | null;
  /** For an owner of the customer's GitHub account: installs the App, connects nothing. */
  owner_install_url: string | null;
  /** GitHub sign-in that connects an installation which already exists. */
  connect_url: string | null;
  can_manage: boolean;
};

export type GitHubRepo = { full_name: string; default_branch: string; private: boolean };

/** `pick` makes `connect_url` come back with that installation chosen. */
export async function getGitHubStatus(orgId: string, pick?: number): Promise<GitHubStatus> {
  const res = await apiFetch(orgId, pick ? `/v1/settings/github?pick=${pick}` : "/v1/settings/github");
  if (!res.ok) throw new Error(`API error ${res.status}`);
  return res.json();
}

export async function listGitHubRepos(orgId: string): Promise<GitHubRepo[]> {
  const res = await apiFetch(orgId, "/v1/settings/github/repos");
  if (!res.ok) return [];
  return res.json();
}
