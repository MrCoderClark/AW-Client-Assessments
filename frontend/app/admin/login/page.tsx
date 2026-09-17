"use client";

import {
  LoginHeading,
  LoginShell,
  PasswordLoginForm,
} from "../../_components/login-card";

/** Local (non-SSO) sign-in. Kept off /login on purpose: Entra is the only
 *  path offered to normal users, and this page is for break-glass /
 *  service accounts that still carry a password. */
export default function AdminLoginPage() {
  return (
    <LoginShell>
      <LoginHeading
        title="Local sign-in"
        subtitle="For accounts that don't sign in through Microsoft."
      />
      <PasswordLoginForm showForgot />
      <a href="/login" className="mute"
         style={{ display: "block", textAlign: "center", marginTop: 14, fontSize: 12, textDecoration: "none" }}>
        Sign in with Microsoft instead
      </a>
    </LoginShell>
  );
}
