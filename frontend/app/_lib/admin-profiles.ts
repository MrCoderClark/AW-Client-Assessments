// Admin /profiles API client. Same-origin via Next rewrite; apiFetch handles Bearer + silent-refresh.
import { apiFetch } from "./auth";

export type LayoutKey = "ops_default" | "fleet_health" | "records";

// Human labels for the layout dropdown / drawer display. Keep in sync with
// auth/profile_routes.py::LAYOUT_KEYS.
export const LAYOUT_OPTIONS: { value: LayoutKey; label: string; hint: string }[] = [
  { value: "ops_default",  label: "Operations",   hint: "Scan/commit workflow — stat tiles, quick actions, recent files." },
  { value: "fleet_health", label: "Fleet Health", hint: "PC-first: health grid + recent scan runs." },
  { value: "records",      label: "Records",      hint: "Read-only file summary. No quick actions." },
];

export type ProfileRow = {
  id: string;
  name: string;
  description: string | null;
  layout_key: LayoutKey;
  is_system: boolean;
  user_count: number;
  created_at: string;
  updated_at: string;
};

async function json<T>(r: Response): Promise<T> {
  if (!r.ok) {
    const j = await r.json().catch(() => ({}));
    throw new Error(j.detail || j.title || `HTTP ${r.status}`);
  }
  return r.json() as Promise<T>;
}

export async function listProfiles(): Promise<ProfileRow[]> {
  const r = await json<{ profiles: ProfileRow[] }>(await apiFetch("/api/v1/profiles"));
  return r.profiles;
}

export async function createProfile(body: {
  name: string; description: string | null; layout_key: LayoutKey;
}): Promise<ProfileRow> {
  return json(await apiFetch("/api/v1/profiles", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  }));
}

export async function updateProfile(id: string, patch: Partial<{
  name: string; description: string | null; layout_key: LayoutKey;
}>): Promise<ProfileRow> {
  return json(await apiFetch(`/api/v1/profiles/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  }));
}

export async function deleteProfile(id: string): Promise<void> {
  const r = await apiFetch(`/api/v1/profiles/${id}`, { method: "DELETE" });
  if (!r.ok) {
    const j = await r.json().catch(() => ({}));
    throw new Error(j.detail || j.title || `HTTP ${r.status}`);
  }
}
