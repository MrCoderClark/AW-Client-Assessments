"use client";

import { usePathname, useRouter } from "next/navigation";
import { createContext, useContext, useEffect, useState } from "react";
import { apiFetch, login as doLogin, logout as doLogout, Me, refresh } from "../_lib/auth";

type Status = "boot" | "anonymous" | "authenticated";

type AuthCtx = {
  status: Status;
  me: Me | null;
  login: (email: string, password: string, remember: boolean) => Promise<void>;
  logout: () => Promise<void>;
  reloadMe: () => Promise<void>;
};

const Ctx = createContext<AuthCtx | null>(null);

const PUBLIC_ROUTES = new Set([
  "/login",
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
      router.replace(`/login?next=${encodeURIComponent(pathname)}`);
    } else if (status === "authenticated" && pathname === "/login") {
      router.replace("/");
    }
  }, [status, pathname, router]);

  const login = async (email: string, password: string, remember: boolean) => {
    const m = await doLogin(email, password, remember);
    setMe(m);
    setStatus("authenticated");
  };

  const logout = async () => {
    await doLogout();
    setMe(null);
    setStatus("anonymous");
    router.replace("/login");
  };

  return (
    <Ctx.Provider value={{ status, me, login, logout, reloadMe }}>
      {children}
    </Ctx.Provider>
  );
}

export function useAuth(): AuthCtx {
  const v = useContext(Ctx);
  if (!v) throw new Error("useAuth outside AuthProvider");
  return v;
}
