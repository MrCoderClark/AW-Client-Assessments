"use client";

import { useState } from "react";
import { useAuth } from "../_components/auth-provider";
import { Logo } from "../_components/logo";

export default function LoginPage() {
  const { login, status } = useAuth();
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
      await login(email.trim(), password, remember);
      // AuthProvider's effect will redirect on status change.
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div style={{
      minHeight: "100vh", display: "grid", placeItems: "center",
      background: "var(--bg)", padding: 24,
    }}>
      <form onSubmit={onSubmit} style={{
        width: "min(400px, 100%)",
        background: "var(--surface)",
        border: "1px solid var(--border-strong)",
        borderRadius: 4,
        padding: "28px 28px 22px",
        boxShadow: "0 24px 60px rgba(15,23,41,0.10)",
      }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 22 }}>
          <Logo />
          <span style={{ fontWeight: 600, fontSize: 15, letterSpacing: "-0.01em" }}>
            Client Viewer
          </span>
        </div>

        <h1 style={{ fontSize: 20, fontWeight: 600, marginBottom: 6 }}>Sign in</h1>
        <p className="mute" style={{ fontSize: 12.5, marginBottom: 22 }}>
          Contact an administrator if you don&apos;t have an account.
        </p>

        <label style={{ display: "block", fontSize: 11.5, color: "var(--muted)", marginBottom: 4, letterSpacing: "0.02em", textTransform: "uppercase" }}>
          Email
        </label>
        <input
          type="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          autoComplete="username"
          autoFocus
          required
          style={inputStyle}
        />

        <label style={{ display: "block", fontSize: 11.5, color: "var(--muted)", marginBottom: 4, marginTop: 14, letterSpacing: "0.02em", textTransform: "uppercase" }}>
          Password
        </label>
        <input
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          autoComplete="current-password"
          required
          style={inputStyle}
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

        <a href="/forgot-password" className="mute"
           style={{ display: "block", textAlign: "center", marginTop: 14, fontSize: 12, textDecoration: "none" }}>
          Forgot your password?
        </a>
      </form>
    </div>
  );
}

const inputStyle: React.CSSProperties = {
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
