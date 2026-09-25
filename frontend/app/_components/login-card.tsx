"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { useAuth } from "./auth-provider";
import { Logo } from "./logo";

export const loginInputStyle: React.CSSProperties = {
  width: "100%",
  height: 36,
  padding: "0 12px",
  fontSize: 13,
  background: "var(--bg)",
  border: "1px solid var(--border-strong)",
  borderRadius: 3,
  color: "var(--ink)",
  outline: "none",
};

const labelStyle: React.CSSProperties = {
  display: "block",
  fontSize: 11.5,
  color: "var(--muted)",
  marginBottom: 4,
  letterSpacing: "0.02em",
  textTransform: "uppercase",
};

export function LoginShell({ children }: { children: React.ReactNode }) {
  return (
    <div className="login-split">
      <aside className="login-brand">
        <div>
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img className="login-brand-logo" src="/aw-general-logo.png"
            alt="America Works — Network of Companies" />
          <h2 className="login-brand-title">
            Client assessments,<br />organized and<br />
            <span className="accent">secure.</span>
          </h2>
          <p className="login-brand-sub">
            Discover, classify, and archive client assessment PDFs across every
            lab PC — from one place.
          </p>
        </div>
        <div className="login-brand-feats">
          <span className="feat"><IconClassify /> Classified</span>
          <span className="sep" />
          <span className="feat"><IconArchive /> Archived</span>
          <span className="sep" />
          <span className="feat"><IconAudit /> Audited</span>
        </div>
      </aside>

      <main className="login-right">
        <div className="login-arc" aria-hidden />
        <div className="login-card">
          <div className="login-card-brand">
            <Logo />
            <span>Assessments Viewer</span>
          </div>
          {children}
        </div>
      </main>
    </div>
  );
}

// Small brand-panel glyphs matching the file lifecycle (stroke = currentColor).
function IconClassify() {   // a document → discovered + classified/renamed
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" />
      <path d="M14 3v5h5" /><path d="M9 13h6M9 17h4" />
    </svg>
  );
}
function IconArchive() {    // a box → committed to the network share
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <rect x="3" y="4" width="18" height="4" rx="1" />
      <path d="M5 8v11a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V8" /><path d="M10 12h4" />
    </svg>
  );
}
function IconAudit() {      // a checked clipboard → hash-chained audit trail
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M8 5H6a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7a2 2 0 0 0-2-2h-2" />
      <rect x="8" y="3" width="8" height="4" rx="1" />
      <path d="M9 14l2 2 4-4" />
    </svg>
  );
}

export function LoginHeading({ title, subtitle }: { title: string; subtitle: string }) {
  return (
    <>
      <h1 style={{ fontSize: 20, fontWeight: 600, marginBottom: 6 }}>{title}</h1>
      <p className="mute" style={{ fontSize: 12.5, marginBottom: 22 }}>{subtitle}</p>
    </>
  );
}

export function LoginError({ children }: { children: React.ReactNode }) {
  return (
    <div style={{
      marginBottom: 18, padding: "8px 12px",
      background: "var(--err-soft)", color: "var(--err)",
      borderRadius: 3, fontSize: 12,
    }}>
      {children}
    </div>
  );
}

/** Email + password sign-in. Used by the local-account page, and by /login
 *  as a fallback when SSO is not configured. */
export function PasswordLoginForm({ showForgot }: { showForgot: boolean }) {
  const { login, status } = useAuth();
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [remember, setRemember] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setErr(null);
    setBusy(true);
    try {
      const out = await login(email.trim(), password, remember);
      if (out.kind === "mfa") router.push("/mfa");
      // else: AuthProvider's effect redirects on status change.
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <form onSubmit={onSubmit}>
      <label style={labelStyle}>Email</label>
      <input
        type="email"
        value={email}
        onChange={(e) => setEmail(e.target.value)}
        autoComplete="username"
        autoFocus
        required
        style={loginInputStyle}
      />

      <label style={{ ...labelStyle, marginTop: 14 }}>Password</label>
      <input
        type="password"
        value={password}
        onChange={(e) => setPassword(e.target.value)}
        autoComplete="current-password"
        required
        style={loginInputStyle}
      />

      <label style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 14, cursor: "pointer", fontSize: 12.5 }}>
        <input type="checkbox" checked={remember} onChange={(e) => setRemember(e.target.checked)}
          style={{ width: 14, height: 14, cursor: "pointer" }} />
        Keep me signed in
      </label>

      {err && (
        <div style={{
          marginTop: 14, padding: "8px 12px",
          background: "var(--err-soft)", color: "var(--err)",
          borderRadius: 3, fontSize: 12, fontFamily: "var(--font-mono)",
        }}>
          {err}
        </div>
      )}

      <button
        type="submit"
        disabled={busy || status === "boot"}
        className="btn btn-primary"
        style={{ marginTop: 20, width: "100%", justifyContent: "center", height: 38, fontSize: 13 }}
      >
        {busy ? "Signing in…" : "Sign in"}
      </button>

      {showForgot && (
        <a href="/forgot-password" className="mute"
          style={{ display: "block", textAlign: "center", marginTop: 14, fontSize: 12, textDecoration: "none" }}>
          Forgot your password?
        </a>
      )}
    </form>
  );
}

export function MicrosoftLogo() {
  return (
    <svg width="16" height="16" viewBox="0 0 21 21" aria-hidden="true">
      <rect x="1" y="1" width="9" height="9" fill="#f25022" />
      <rect x="11" y="1" width="9" height="9" fill="#7fba00" />
      <rect x="1" y="11" width="9" height="9" fill="#00a4ef" />
      <rect x="11" y="11" width="9" height="9" fill="#ffb900" />
    </svg>
  );
}
