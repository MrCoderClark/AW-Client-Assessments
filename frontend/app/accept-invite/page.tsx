"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { acceptInvite } from "../_lib/auth";
import { Card, ErrBox, ErrorCard, inputStyle, Row } from "../_components/auth-card";

function InnerAcceptInvite() {
  const params = useSearchParams();
  const router = useRouter();
  const token = params.get("token") ?? "";
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setErr(null);
    if (password !== confirm) { setErr("Passwords don't match."); return; }
    setBusy(true);
    try {
      await acceptInvite(token, password);
      router.replace("/");
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  if (!token) {
    return <ErrorCard title="Missing invitation" body="The invite link is missing its token. Ask your admin for a fresh invite." />;
  }

  return (
    <Card>
      <h1 style={{ fontSize: 20, fontWeight: 600, marginBottom: 6 }}>Set up your account</h1>
      <p className="mute" style={{ fontSize: 12.5, marginBottom: 22 }}>
        Choose a password to activate your Client Viewer account.
      </p>

      <form onSubmit={submit}>
        <Row label="Password">
          <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} required autoComplete="new-password" style={inputStyle} autoFocus />
        </Row>
        <Row label="Confirm password">
          <input type="password" value={confirm} onChange={(e) => setConfirm(e.target.value)} required autoComplete="new-password" style={inputStyle} />
        </Row>
        <div className="mute" style={{ fontSize: 11.5, marginTop: 8 }}>
          At least 12 characters and 3 of: upper, lower, digit, symbol.
        </div>

        {err && <ErrBox>{err}</ErrBox>}

        <button type="submit" disabled={busy} className="btn btn-primary"
                style={{ marginTop: 20, width: "100%", justifyContent: "center", height: 38, fontSize: 13 }}>
          {busy ? "Activating…" : "Activate & sign in"}
        </button>
      </form>
    </Card>
  );
}

export default function AcceptInvitePage() {
  return (
    <Suspense fallback={<Card><p className="mute">Loading…</p></Card>}>
      <InnerAcceptInvite />
    </Suspense>
  );
}
