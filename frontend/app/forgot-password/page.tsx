"use client";

import { useState } from "react";
import { forgotPassword } from "../_lib/auth";
import { Card, ErrBox, inputStyle, OkBox, Row } from "../_components/auth-card";

export default function ForgotPasswordPage() {
  const [email, setEmail] = useState("");
  const [sent, setSent] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setErr(null);
    setBusy(true);
    try {
      await forgotPassword(email.trim());
      setSent(true);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card>
      <h1 style={{ fontSize: 20, fontWeight: 600, marginBottom: 6 }}>Reset your password</h1>
      <p className="mute" style={{ fontSize: 12.5, marginBottom: 22 }}>
        Enter your email address. If it&apos;s registered, we&apos;ll send you a reset link that
        expires in 30 minutes.
      </p>

      {sent ? (
        <>
          <OkBox>
            If <span className="mono">{email}</span> matches an account, a reset link is on its way.
            Check your inbox (and spam).
          </OkBox>
          <a href="/login" className="btn" style={{ marginTop: 20, width: "100%", justifyContent: "center", height: 36 }}>
            Back to sign in
          </a>
        </>
      ) : (
        <form onSubmit={submit}>
          <Row label="Email">
            <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} required autoFocus style={inputStyle} />
          </Row>
          {err && <ErrBox>{err}</ErrBox>}
          <button type="submit" disabled={busy} className="btn btn-primary"
                  style={{ marginTop: 20, width: "100%", justifyContent: "center", height: 38, fontSize: 13 }}>
            {busy ? "Sending…" : "Send reset link"}
          </button>
          <a href="/login" className="mute" style={{ display: "block", textAlign: "center", marginTop: 14, fontSize: 12, textDecoration: "none" }}>
            ← Back to sign in
          </a>
        </form>
      )}
    </Card>
  );
}
