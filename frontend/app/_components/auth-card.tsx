"use client";

import { Logo } from "./logo";

export function Card({ children }: { children: React.ReactNode }) {
  return (
    <div style={{ minHeight: "100vh", display: "grid", placeItems: "center", background: "var(--bg)", padding: 24 }}>
      <div style={{
        width: "min(420px, 100%)", background: "var(--surface)",
        border: "1px solid var(--border-strong)", borderRadius: 4,
        padding: "28px 28px 22px",
        boxShadow: "0 24px 60px rgba(15,23,41,0.10)",
      }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 22 }}>
          <Logo />
          <span style={{ fontWeight: 600, fontSize: 15, letterSpacing: "-0.01em" }}>Client Viewer</span>
        </div>
        {children}
      </div>
    </div>
  );
}

export function ErrorCard({ title, body }: { title: string; body: string }) {
  return (
    <Card>
      <h1 style={{ fontSize: 18, fontWeight: 600, marginBottom: 8, color: "var(--err)" }}>{title}</h1>
      <p className="mute" style={{ fontSize: 13 }}>{body}</p>
      <a href="/login" className="btn" style={{ marginTop: 20, width: "100%", justifyContent: "center", height: 36 }}>Back to sign in</a>
    </Card>
  );
}

export function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div style={{ marginTop: 14 }}>
      <label style={{ display: "block", fontSize: 11.5, color: "var(--muted)", marginBottom: 4, letterSpacing: "0.02em", textTransform: "uppercase" }}>
        {label}
      </label>
      {children}
    </div>
  );
}

export function ErrBox({ children }: { children: React.ReactNode }) {
  return (
    <div style={{
      marginTop: 14, padding: "8px 12px",
      background: "var(--err-soft)", color: "var(--err)",
      borderRadius: 3, fontSize: 12, fontFamily: "var(--font-mono)",
    }}>{children}</div>
  );
}

export function OkBox({ children }: { children: React.ReactNode }) {
  return (
    <div style={{
      marginTop: 14, padding: "8px 12px",
      background: "color-mix(in oklab, var(--ok) 12%, transparent)", color: "var(--ok)",
      borderRadius: 3, fontSize: 12.5,
    }}>{children}</div>
  );
}

export const inputStyle: React.CSSProperties = {
  width: "100%", height: 36, padding: "0 12px", fontSize: 13,
  background: "var(--bg)", border: "1px solid var(--border-strong)",
  borderRadius: 3, color: "var(--ink)", outline: "none",
};
