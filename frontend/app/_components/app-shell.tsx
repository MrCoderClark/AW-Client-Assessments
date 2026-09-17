"use client";

import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { LogDrawer } from "./log-drawer";
import { Sidebar } from "./sidebar";
import { Topbar } from "./topbar";
import { useAuth } from "./auth-provider";

const PUBLIC_ROUTES = new Set([
  "/login",
  "/admin/login",
  "/accept-invite",
  "/verify-email",
  "/forgot-password",
  "/reset-password",
]);

export function AppShell({ children }: { children: React.ReactNode }) {
  const { status } = useAuth();
  const pathname = usePathname();
  const isPublic = PUBLIC_ROUTES.has(pathname);

  // Mobile off-canvas nav. Lives here so the Topbar's hamburger and the
  // Sidebar drawer share one source of truth; auto-closes on navigation.
  const [navOpen, setNavOpen] = useState(false);
  useEffect(() => { setNavOpen(false); }, [pathname]);

  // Bootstrapping: render nothing (avoids a login-page flash before silent-refresh finishes).
  if (status === "boot" && !isPublic) {
    return (
      <div style={{
        minHeight: "100vh", display: "grid", placeItems: "center",
        background: "var(--bg)", color: "var(--muted)", fontSize: 12,
      }}>
        Loading…
      </div>
    );
  }

  // Public route (login page): render bare, no shell.
  if (isPublic) return <>{children}</>;

  // Authenticated: full app shell.
  return (
    <>
      <div className="shell">
        <Sidebar mobileOpen={navOpen} onClose={() => setNavOpen(false)} />
        <div className="main">
          <Topbar onMenuClick={() => setNavOpen(true)} />
          <div className="workspace">{children}</div>
        </div>
      </div>
      <LogDrawer />
    </>
  );
}
