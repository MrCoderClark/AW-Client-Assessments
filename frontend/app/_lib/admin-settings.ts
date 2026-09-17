// Admin runtime settings + SSO allowlist client. Same-origin via Next rewrite.
import { apiFetch } from "./auth";

export type AppSettings = {
  sso_login_enabled: boolean;
  sso_allowlist_enabled: boolean;
  mfa_required: boolean;
};

export type AllowEntry = { email: string; note: string | null; added_at: string | null };
export type AllowPage = { emails: AllowEntry[]; total: number; limit: number; offset: number };
export type AllowAddResult = { added: number; skipped: number; invalid: string[] };

async function json<T>(r: Response): Promise<T> {
  if (!r.ok) {
    const j = await r.json().catch(() => ({}));
    throw new Error(j.detail || j.title || `HTTP ${r.status}`);
  }
  return r.json() as Promise<T>;
}

export async function getSettings(): Promise<AppSettings> {
  return json(await apiFetch("/api/v1/settings"));
}

export async function patchSettings(patch: Partial<AppSettings>): Promise<AppSettings> {
  return json(await apiFetch("/api/v1/settings", {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  }));
}

export async function getAllowlist(params: { q?: string; limit?: number; offset?: number } = {}): Promise<AllowPage> {
  const qs = new URLSearchParams();
  if (params.q) qs.set("q", params.q);
  qs.set("limit", String(params.limit ?? 50));
  qs.set("offset", String(params.offset ?? 0));
  return json(await apiFetch(`/api/v1/settings/sso-allowlist?${qs.toString()}`));
}

export async function addAllowlist(emails: string[], note?: string): Promise<AllowAddResult> {
  return json(await apiFetch("/api/v1/settings/sso-allowlist", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ emails, note: note || null }),
  }));
}

export async function removeAllowlist(email: string): Promise<void> {
  const r = await apiFetch(`/api/v1/settings/sso-allowlist/${encodeURIComponent(email)}`, { method: "DELETE" });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
}
