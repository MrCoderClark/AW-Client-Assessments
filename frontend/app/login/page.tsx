"use client";

import { useEffect, useState } from "react";
import {
  LoginError,
  LoginHeading,
  LoginShell,
  MicrosoftLogo,
  PasswordLoginForm,
} from "../_components/login-card";

// Coarse, non-leaky messages for the ?sso_error= reasons the callback redirects with.
function ssoErrorMessage(reason: string): string {
  switch (reason) {
    case "state":
      return "Microsoft sign-in expired or was interrupted. Please try again.";
    case "disabled":
      return "Your account isn't permitted to sign in. Contact IT Support.";
    case "not_allowed":
      return "This account isn't authorized to use Client Files Viewer. Please contact IT Support to request access.";
    case "sso_off":
      return "Microsoft sign-in is currently turned off. Use a local account.";
    case "cancelled":
      return "Microsoft sign-in was cancelled.";
    default:
      return "Microsoft sign-in failed. Please try again.";
  }
}

export default function LoginPage() {
  // `null` = still asking the backend; avoids flashing the password fallback
  // for a moment on a tenant where SSO is the only permitted path.
  const [ssoEnabled, setSsoEnabled] = useState<boolean | null>(null);
  const [ssoError, setSsoError] = useState<string | null>(null);

  useEffect(() => {
    // Read ?sso_error= straight off the URL (avoids the Suspense boundary that
    // useSearchParams requires) and clean it out of the address bar.
    try {
      const url = new URL(window.location.href);
      const reason = url.searchParams.get("sso_error");
      if (reason) {
        setSsoError(ssoErrorMessage(reason));
        url.searchParams.delete("sso_error");
        window.history.replaceState({}, "", url.pathname + url.search);
      }
    } catch {
      /* no window / malformed URL — ignore */
    }
    // Ask the backend whether SSO is configured, so we only show the button when it works.
    let alive = true;
    fetch("/api/v1/auth/sso/config")
      .then((r) => (r.ok ? r.json() : { enabled: false }))
      .then((d) => { if (alive) setSsoEnabled(Boolean(d?.enabled)); })
      .catch(() => { if (alive) setSsoEnabled(false); });
    return () => { alive = false; };
  }, []);

  return (
    <LoginShell>
      <LoginHeading
        title="Sign in"
        subtitle="Use your work account. Contact IT Support if you need help signing in."
      />

      {ssoError && <LoginError>{ssoError}</LoginError>}

      {ssoEnabled !== false && (
        <>
          <a
            href="/api/v1/auth/sso/login"
            aria-disabled={ssoEnabled === null}
            style={{
              display: "flex", alignItems: "center", justifyContent: "center", gap: 10,
              width: "100%", height: 46, fontSize: 15, fontWeight: 600,
              color: "#ffffff", background: "var(--accent)",
              borderRadius: 8, textDecoration: "none",
              boxShadow: "0 6px 16px rgba(37,99,235,0.28)",
              opacity: ssoEnabled === null ? 0.6 : 1,
              pointerEvents: ssoEnabled === null ? "none" : undefined,
            }}
          >
            <MicrosoftLogo />
            Sign in with Microsoft
          </a>


        </>
      )}

      {/* SSO not configured — fall back to the local form so the app is never
          lockable from its own login page. */}
      {ssoEnabled === false && <PasswordLoginForm showForgot />}
    </LoginShell>
  );
}
