"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { resetPassword } from "../_lib/auth";
import { Card, ErrBox, ErrorCard, inputStyle, OkBox, Row } from "../_components/auth-card";

function InnerResetPassword() {
  const params = useSearchParams();
  const router = useRouter();
  const token = params.get("token") ?? "";
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [done, setDone] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setErr(null);
    if (password !== confirm) { setErr("Passwords don't match."); return; }
    setBusy(true);
    try {
      await resetPassword(token, password);
      setDone(true);
      setTimeout(() => router.replace("/login"), 2000);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  if (!token) {
    return <ErrorCard title="Missing reset link" body="The reset link is missing its token. Request a new one from the sign-in page." />;
  }

  return (
    <Card>
      <h1 style={{ fontSize: 20, fontWeight: 600, marginBottom: 6 }}>Choose a new password</h1>
      <p className="mute" style={{ fontSize: 12.5, marginBottom: 22 }}>
        Your existing sessions will be signed out.
      </p>

      {done ? (
        <>
          <OkBox>Password updated. Redirecting to sign in…</OkBox>
        </>
      ) : (
        <form onSubmit={submit}>
          <Row label="New password">
            <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} required autoFocus autoComplete="new-password" style={inputStyle} />
          </Row>
          <Row label="Confirm">
            <input type="password" value={confirm} onChange={(e) => setConfirm(e.target.value)} required autoComplete="new-password" style={inputStyle} />
          </Row>
          <div className="mute" style={{ fontSize: 11.5, marginTop: 8 }}>
            At least 12 characters and 3 of: upper, lower, digit, symbol.
          </div>
          {err && <ErrBox>{err}</ErrBox>}
          <button type="submit" disabled={busy} className="btn btn-primary"
                  style={{ marginTop: 20, width: "100%", justifyContent: "center", height: 38, fontSize: 13 }}>
            {busy ? "Saving…" : "Set new password"}
          </button>
        </form>
      )}
    </Card>
  );
}

export default function ResetPasswordPage() {
  return (
    <Suspense fallback={<Card><p className="mute">Loading…</p></Card>}>
      <InnerResetPassword />
    </Suspense>
  );
}
