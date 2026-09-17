"use client";

import { usePathname, useRouter } from "next/navigation";
import { createContext, useContext, useEffect, useState } from "react";
import { apiFetch, login as doLogin, LoginOutcome, logout as doLogout, Me, refresh } from "../_lib/auth";

type Status = "boot" | "anonymous" | "authenticated";

type AuthCtx = {
  status: Status;
  me: Me | null;
  login: (email: string, password: string, remember: boolean) => Promise<LoginOutcome>;
  logout: () => Promise<void>;
  reloadMe: () => Promise<void>;
  /** Seat the session after an MFA challenge completes (from the /mfa page). */
  finishMfa: (me: Me) => void;
};

const Ctx = createContext<AuthCtx | null>(null);

// Both login surfaces are public: /login offers Entra SSO, /admin/login the
// local email+password form for accounts Microsoft doesn't manage.
const LOGIN_ROUTES = new Set(["/login", "/admin/login"]);

const PUBLIC_ROUTES = new Set([
  "/login",
  "/admin/login",
  "/mfa",
  "/accept-invite",
  "/verify-email",
  "/forgot-password",
  "/reset-password",
]);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [status, setStatus] = useState<Status>("boot");
  const [me, setMe] = useState<Me | null>(null);
  const router = useRouter();
  const pathname = usePathname();

  const reloadMe = async () => {
    const r = await apiFetch("/api/v1/auth/me");
    if (!r.ok) throw new Error("me failed");
    setMe((await r.json()) as Me);
  };

  // Bootstrap on first mount: try a silent refresh; if it succeeds, load /me.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      const ok = await refresh();
      if (cancelled) return;
      if (ok) {
        try {
          await reloadMe();
          setStatus("authenticated");
          return;
        } catch { /* fall through */ }
      }
      setStatus("anonymous");
    })();
    return () => { cancelled = true; };
  }, []);

  // Route guard — kick to /login when anonymous on a protected route,
  // bounce to / when authenticated on /login.
  useEffect(() => {
    if (status === "boot") return;
    const onPublic = PUBLIC_ROUTES.has(pathname);
    if (status === "anonymous" && !onPublic) {
      // Only carry ?next when it points somewhere other than the default
      // landing — no ugly ?next=%2F when the target is just "/".
      const dest = pathname && pathname !== "/"
        ? `/login?next=${encodeURIComponent(pathname)}`
        : "/login";
      router.replace(dest);
    } else if (status === "authenticated" && LOGIN_ROUTES.has(pathname)) {
      // Honor ?next on the way back in (must be an internal path — never an
      // absolute/protocol-relative URL, to avoid an open redirect).
      let next = "/";
      try {
        const raw = new URLSearchParams(window.location.search).get("next");
        if (raw && raw.startsWith("/") && !raw.startsWith("//")) next = raw;
      } catch { /* no window / bad query — fall back to "/" */ }
      router.replace(next);
    }
  }, [status, pathname, router]);

  const login = async (email: string, password: string, remember: boolean) => {
    const out = await doLogin(email, password, remember);
    if (out.kind === "session") {
      setMe(out.me);
      setStatus("authenticated");
    }
    // kind === "mfa": caller routes to /mfa; no session yet.
    return out;
  };

  const finishMfa = (m: Me) => {
    setMe(m);
    setStatus("authenticated");
  };

  const logout = async () => {
    const isSso = me?.sso === true;
    // Drop the in-memory access token before we go, either way.
    setMe(null);
    setStatus("anonymous");
    if (isSso) {
      // Full-page navigation (not router.replace): GET /sso/logout revokes the
      // CFV session, then 302s through Entra's end_session so Microsoft drops
      // its session too — otherwise the next visitor is silently re-authed on a
      // shared lab PC. Entra returns the browser to /login.
      window.location.href = "/api/v1/auth/sso/logout";
      return;
    }
    // Local account: revoke server-side, then back to the local login form.
    await doLogout();
    router.replace("/admin/login");
  };

  return (
    <Ctx.Provider value={{ status, me, login, logout, reloadMe, finishMfa }}>
      {children}
    </Ctx.Provider>
  );
}

export function useAuth(): AuthCtx {
  const v = useContext(Ctx);
  if (!v) throw new Error("useAuth outside AuthProvider");
  return v;
}
