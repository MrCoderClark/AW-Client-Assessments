"use client";

import { usePathname } from "next/navigation";
import { LogDrawer } from "./log-drawer";
import { Sidebar } from "./sidebar";
import { Topbar } from "./topbar";
import { useAuth } from "./auth-provider";

const PUBLIC_ROUTES = new Set([
  "/login",
  "/accept-invite",
  "/verify-email",
  "/forgot-password",
  "/reset-password",
]);

export function AppShell({ children }: { children: React.ReactNode }) {
  const { status } = useAuth();
  const pathname = usePathname();
  const isPublic = PUBLIC_ROUTES.has(pathname);

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
        <Sidebar />
        <div className="main">
          <Topbar />
          <div className="workspace">{children}</div>
        </div>
      </div>
      <LogDrawer />
    </>
  );
}
