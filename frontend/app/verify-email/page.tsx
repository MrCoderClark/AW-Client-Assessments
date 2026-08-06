"use client";

import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import { verifyEmail } from "../_lib/auth";
import { Card, ErrBox, ErrorCard, OkBox } from "../_components/auth-card";

function InnerVerifyEmail() {
  const params = useSearchParams();
  const token = params.get("token") ?? "";
  const [state, setState] = useState<"pending" | "ok" | "err">("pending");
  const [msg, setMsg] = useState("");

  useEffect(() => {
    if (!token) return;
    (async () => {
      try {
        await verifyEmail(token);
        setState("ok");
      } catch (e) {
        setState("err");
        setMsg(e instanceof Error ? e.message : String(e));
      }
    })();
  }, [token]);

  if (!token) {
    return <ErrorCard title="Missing verification link" body="The link is missing its token. Ask an admin to resend it." />;
  }

  return (
    <Card>
      <h1 style={{ fontSize: 20, fontWeight: 600, marginBottom: 6 }}>Verify email</h1>
      {state === "pending" && <p className="mute" style={{ fontSize: 13 }}>Verifying…</p>}
      {state === "ok" && (
        <>
          <OkBox>Email verified. You can now sign in.</OkBox>
          <a href="/login" className="btn btn-primary" style={{ marginTop: 20, width: "100%", justifyContent: "center", height: 38, fontSize: 13 }}>
            Continue to sign in
          </a>
        </>
      )}
      {state === "err" && (
        <>
          <ErrBox>{msg}</ErrBox>
          <a href="/login" className="btn" style={{ marginTop: 20, width: "100%", justifyContent: "center", height: 36 }}>
            Back to sign in
          </a>
        </>
      )}
    </Card>
  );
}

export default function VerifyEmailPage() {
  return (
    <Suspense fallback={<Card><p className="mute">Loading…</p></Card>}>
      <InnerVerifyEmail />
    </Suspense>
  );
}
