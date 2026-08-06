// Client-side auth state, apiFetch wrapper, login/logout/refresh.
//
// The access token lives in module memory (never localStorage — §FR-AUTH-14).
// The refresh cookie is handled by the browser (HttpOnly). On every 401 that
// isn't itself a login/refresh, apiFetch quietly refreshes and retries once.
// If the refresh fails, the store clears and the login page renders.

type Listener = () => void;

type AccessState = {
  token: string | null;
  expiresAt: number; // epoch ms
};

let state: AccessState = { token: null, expiresAt: 0 };
const listeners = new Set<Listener>();

function set(next: AccessState) {
  state = next;
  listeners.forEach((l) => l());
}

export function getAccessToken(): string | null {
  return state.token;
}

export function subscribe(l: Listener): () => void {
  listeners.add(l);
  return () => { listeners.delete(l); };
}

export function snapshot(): AccessState {
  return state;
}

// -------- API surface --------

export type LoginResponse = {
  access_token: string;
  token_type: "Bearer";
  expires_in: number;
  refresh_token: string;
};

export type Me = {
  user_id: string;
  email: string;
  first_name: string | null;
  last_name: string | null;
  display_name: string | null;
  role: string;
  status: string;
  permissions: string[];
  must_change_password: boolean;
  profile: { id: string; name: string; layout_key: string } | null;
  /** Per-user widget composition override. null = use profile default. */
  dashboard_widgets: string[] | null;
};

export async function saveDashboardWidgets(widgets: string[] | null): Promise<Me> {
  const r = await apiFetch("/api/v1/auth/me/dashboard", {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ widgets }),
  });
  if (!r.ok) {
    const j = await r.json().catch(() => ({}));
    throw new Error(j.detail || j.title || `HTTP ${r.status}`);
  }
  return r.json();
}

const SAFE_METHODS = new Set(["GET", "HEAD", "OPTIONS"]);

function readCookie(name: string): string | null {
  if (typeof document === "undefined") return null;
  const prefix = `${name}=`;
  for (const raw of document.cookie.split(";")) {
    const c = raw.trim();
    if (c.startsWith(prefix)) return decodeURIComponent(c.slice(prefix.length));
  }
  return null;
}

async function raw(path: string, init?: RequestInit): Promise<Response> {
  // For unsafe methods without a Bearer token, echo the double-submit CSRF cookie
  // into the X-CSRF-Token header so the backend's CsrfMiddleware lets it through.
  const method = (init?.method ?? "GET").toUpperCase();
  const headers = new Headers(init?.headers || {});
  if (!SAFE_METHODS.has(method) && !headers.has("Authorization")) {
    const t = readCookie("cfv_csrf");
    if (t && !headers.has("X-CSRF-Token")) headers.set("X-CSRF-Token", t);
  }
  return fetch(path, { credentials: "include", ...init, headers });
}

let pendingRefresh: Promise<boolean> | null = null;

// Silent refresh. Multiple concurrent 401s share one in-flight refresh.
export async function refresh(): Promise<boolean> {
  if (pendingRefresh) return pendingRefresh;
  pendingRefresh = (async () => {
    try {
      const r = await raw("/api/v1/auth/refresh", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      });
      if (!r.ok) { set({ token: null, expiresAt: 0 }); return false; }
      const j: LoginResponse = await r.json();
      set({ token: j.access_token, expiresAt: Date.now() + j.expires_in * 1000 });
      return true;
    } finally {
      pendingRefresh = null;
    }
  })();
  return pendingRefresh;
}

export async function login(email: string, password: string, remember: boolean): Promise<Me> {
  const r = await raw("/api/v1/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password, remember }),
  });
  if (!r.ok) {
    const j = await r.json().catch(() => ({}));
    throw new Error(j.detail || j.title || `HTTP ${r.status}`);
  }
  const j: LoginResponse = await r.json();
  set({ token: j.access_token, expiresAt: Date.now() + j.expires_in * 1000 });
  const me = await apiFetch("/api/v1/auth/me").then((rr) => rr.json());
  return me as Me;
}

export async function logout(): Promise<void> {
  try {
    await apiFetch("/api/v1/auth/logout", { method: "POST" });
  } catch { /* ignore */ }
  set({ token: null, expiresAt: 0 });
}

async function postJson(path: string, body: unknown): Promise<Response> {
  return raw(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

async function throwIfBad(r: Response): Promise<void> {
  if (r.ok) return;
  const j = await r.json().catch(() => ({}));
  throw new Error(j.detail || j.title || `HTTP ${r.status}`);
}

export async function acceptInvite(
  token: string, password: string, firstName = "", lastName = "",
): Promise<Me> {
  const r = await postJson("/api/v1/auth/accept-invite", {
    token, password, first_name: firstName, last_name: lastName,
  });
  await throwIfBad(r);
  const j: LoginResponse = await r.json();
  set({ token: j.access_token, expiresAt: Date.now() + j.expires_in * 1000 });
  const me = await apiFetch("/api/v1/auth/me").then((rr) => rr.json());
  return me as Me;
}

export async function verifyEmail(token: string): Promise<void> {
  const r = await postJson("/api/v1/auth/email/verify", { token });
  await throwIfBad(r);
}

export async function forgotPassword(email: string): Promise<void> {
  // 202 comes back whether or not the email exists — we treat any 2xx as success.
  const r = await postJson("/api/v1/auth/password/forgot", { email });
  await throwIfBad(r);
}

export async function resetPassword(token: string, newPassword: string): Promise<void> {
  const r = await postJson("/api/v1/auth/password/reset", { token, new_password: newPassword });
  await throwIfBad(r);
}

export async function changePassword(currentPassword: string, newPassword: string): Promise<void> {
  const r = await apiFetch("/api/v1/auth/password/change", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
  });
  await throwIfBad(r);
  // Password change revokes all sessions server-side → clear state so the app kicks to /login.
  set({ token: null, expiresAt: 0 });
}

// -------- fetch wrapper --------

const AUTH_FREE_PATHS = new Set([
  "/api/v1/auth/login",
  "/api/v1/auth/refresh",
  "/api/v1/auth/jwks",
  "/api/health",
]);

export async function apiFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const headers = new Headers(init.headers || {});
  if (state.token && !AUTH_FREE_PATHS.has(path)) {
    headers.set("Authorization", `Bearer ${state.token}`);
  }
  let r = await raw(path, { ...init, headers });
  if (r.status !== 401 || AUTH_FREE_PATHS.has(path)) return r;

  // Silent-refresh + one retry
  const ok = await refresh();
  if (!ok) return r; // caller sees the original 401; app router redirects to /login
  const retryHeaders = new Headers(init.headers || {});
  if (state.token) retryHeaders.set("Authorization", `Bearer ${state.token}`);
  r = await raw(path, { ...init, headers: retryHeaders });
  return r;
}
