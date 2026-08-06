// Admin /users API client. Same-origin via Next rewrite; apiFetch handles Bearer + silent-refresh.
import { apiFetch } from "./auth";

export type UserRow = {
  id: string;
  email: string;
  first_name: string | null;
  last_name: string | null;
  display_name: string | null;
  role: "admin" | "operator" | "viewer";
  status: "INVITED" | "ACTIVE" | "SUSPENDED" | "DEACTIVATED" | "SOFT_DELETED";
  must_change_password: boolean;
  mfa_enrolled: boolean;
  email_verified: boolean;
  ver: number;
  failed_login_attempts: number;
  locked_until: string | null;
  last_login_at: string | null;
  last_login_ip: string | null;
  suspended_at: string | null;
  suspended_reason: string | null;
  deleted_at: string | null;
  created_at: string;
  updated_at: string;
  profile: { id: string; name: string; layout_key: string } | null;
};

export type UserList = { users: UserRow[]; total: number; limit: number; offset: number };

async function json<T>(r: Response): Promise<T> {
  if (!r.ok) {
    const j = await r.json().catch(() => ({}));
    throw new Error(j.detail || j.title || `HTTP ${r.status}`);
  }
  return r.json() as Promise<T>;
}

export async function listUsers(params: {
  q?: string; status?: string; role?: string; includeDeleted?: boolean;
  limit?: number; offset?: number;
} = {}): Promise<UserList> {
  const qs = new URLSearchParams();
  if (params.q) qs.set("q", params.q);
  if (params.status) qs.set("status", params.status);
  if (params.role) qs.set("role", params.role);
  if (params.includeDeleted) qs.set("include_deleted", "true");
  qs.set("limit", String(params.limit ?? 50));
  qs.set("offset", String(params.offset ?? 0));
  return json(await apiFetch(`/api/v1/users?${qs.toString()}`));
}

export async function getUser(id: string): Promise<UserRow> {
  return json(await apiFetch(`/api/v1/users/${id}`));
}

export async function updateUser(id: string, patch: Partial<{
  first_name: string; last_name: string; display_name: string;
  email: string; role: string;
  /** Empty string clears the profile assignment; a UUID sets it. */
  profile_id: string;
}>): Promise<UserRow> {
  return json(await apiFetch(`/api/v1/users/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  }));
}

export async function suspendUser(id: string, reason: string): Promise<UserRow> {
  return json(await apiFetch(`/api/v1/users/${id}/suspend`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ reason }),
  }));
}

export async function reactivateUser(id: string): Promise<UserRow> {
  return json(await apiFetch(`/api/v1/users/${id}/reactivate`, { method: "POST" }));
}

export async function forceResetUser(id: string): Promise<{
  user_id: string; reset_url: string; mail_ok: boolean; mail_note: string;
}> {
  return json(await apiFetch(`/api/v1/users/${id}/force-reset`, { method: "POST" }));
}

export async function deleteUser(id: string, opts: { hard?: boolean } = {}): Promise<void> {
  const qs = opts.hard ? "?hard=true" : "";
  const r = await apiFetch(`/api/v1/users/${id}${qs}`, { method: "DELETE" });
  if (!r.ok) {
    const j = await r.json().catch(() => ({}));
    throw new Error(j.detail || j.title || `HTTP ${r.status}`);
  }
}

export async function inviteUser(payload: {
  email: string; role: string; first_name: string; last_name: string;
}): Promise<{ user_id: string; invite_url: string; mail_ok: boolean; mail_note: string }> {
  return json(await apiFetch(`/api/v1/auth/users`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  }));
}
