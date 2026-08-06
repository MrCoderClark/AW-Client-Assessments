"use client";

// Client-side route gate. Redirects to `/` when the current user is missing
// any of the required permissions. Backend gating on `require()` is the real
// enforcement — this just keeps non-admins from seeing UIs they can't use.

import { useRouter } from "next/navigation";
import { useEffect } from "react";
import { useAuth } from "./auth-provider";

export function RequirePerm({
  perms, blockedRoles, children, fallback,
}: {
  perms: string[];
  /** Reject users in these roles even if their permissions would let them
   *  through. Used for viewer-hidden pages like /pcs and /logs, where the
   *  API still grants pc:read/log:read (other pages consume it) but the
   *  page itself is off the viewer's UI. */
  blockedRoles?: string[];
  children: React.ReactNode;
  fallback?: React.ReactNode;
}) {
  const { status, me } = useAuth();
  const router = useRouter();

  const has = status === "authenticated" && me
    ? perms.every((p) => me.permissions.includes(p))
      && !(blockedRoles?.includes(me.role) ?? false)
    : null;

  useEffect(() => {
    if (has === false) router.replace("/");
  }, [has, router]);

  if (status === "boot" || has === null) {
    return (
      <div style={{
        minHeight: "60vh", display: "grid", placeItems: "center",
        color: "var(--muted)", fontSize: 12,
      }}>Loading…</div>
    );
  }
  if (!has) return <>{fallback ?? null}</>;
  return <>{children}</>;
}
