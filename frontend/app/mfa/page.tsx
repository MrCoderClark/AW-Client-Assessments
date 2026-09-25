"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import QRCode from "qrcode";
import { useAuth } from "../_components/auth-provider";
import {
  LoginError,
  LoginHeading,
  LoginShell,
  loginInputStyle,
} from "../_components/login-card";
import {
  mfaChallenge,
  mfaEnrollStart,
  mfaEnrollVerify,
  mfaVerify,
} from "../_lib/auth";

type Step = "loading" | "enroll" | "backup" | "verify";
type Method = "totp" | "backup";

const groups = (s: string) => (s.match(/.{1,4}/g) ?? []).join(" ");

export default function MfaPage() {
  const { finishMfa } = useAuth();
  const router = useRouter();
  const [step, setStep] = useState<Step>("loading");
  const [method, setMethod] = useState<Method>("totp");
  const [code, setCode] = useState("");
  const [secret, setSecret] = useState("");
  const [otpauth, setOtpauth] = useState("");
  const [qr, setQr] = useState<string | null>(null);
  const [backupCodes, setBackupCodes] = useState<string[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const started = useRef(false);

  // Read the pending challenge once; kick to /login if there isn't one.
  useEffect(() => {
    if (started.current) return;
    started.current = true;
    (async () => {
      try {
        const { purpose } = await mfaChallenge();
        if (purpose === "enroll") {
          const s = await mfaEnrollStart();
          setSecret(s.secret);
          setOtpauth(s.otpauth_uri);
          setStep("enroll");
        } else {
          setStep("verify");
        }
      } catch {
        router.replace("/login?sso_error=state");
      }
    })();
  }, [router]);

  // Render the QR once we have the otpauth URI.
  useEffect(() => {
    if (!otpauth) return;
    QRCode.toDataURL(otpauth, { width: 220, margin: 1, errorCorrectionLevel: "M" })
      .then(setQr)
      .catch(() => setQr(null));  // fall back to the manual key
  }, [otpauth]);

  const land = (me: Parameters<typeof finishMfa>[0]) => {
    finishMfa(me);
    router.replace("/");
  };

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setErr(null);
    setBusy(true);
    try {
      if (step === "enroll") {
        const { me, backupCodes: codes } = await mfaEnrollVerify(code.trim());
        if (codes.length) {
          setBackupCodes(codes);
          setStep("backup");
          // stash me on the window for the ack step (avoids re-verify)
          (window as unknown as { __mfaMe?: unknown }).__mfaMe = me;
        } else {
          land(me);
        }
      } else {
        const me = await mfaVerify(code.trim(), method);
        land(me);
      }
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
      setCode("");
    } finally {
      setBusy(false);
    }
  };

  if (step === "loading") {
    return (
      <LoginShell>
        <div className="mute" style={{ fontSize: 13, padding: "8px 0" }}>Loading…</div>
      </LoginShell>
    );
  }

  if (step === "backup") {
    return (
      <LoginShell>
        <LoginHeading
          title="Save your backup codes"
          subtitle="Each code works once if you lose your authenticator. Store them somewhere safe — you won't see them again."
        />
        <div style={{
          display: "grid", gridTemplateColumns: "1fr 1fr", gap: "6px 16px",
          fontFamily: "var(--font-mono)", fontSize: 14, padding: "14px 16px",
          background: "var(--bg)", border: "1px solid var(--border)", borderRadius: 6, marginBottom: 18,
        }}>
          {backupCodes.map((c) => <span key={c}>{c}</span>)}
        </div>
        <button
          className="btn btn-primary"
          style={{ width: "100%", justifyContent: "center", height: 40 }}
          onClick={() => {
            const me = (window as unknown as { __mfaMe?: Parameters<typeof finishMfa>[0] }).__mfaMe;
            if (me) land(me);
          }}
        >
          I&apos;ve saved these — continue
        </button>
      </LoginShell>
    );
  }

  const enrolling = step === "enroll";

  return (
    <LoginShell>
      <LoginHeading
        title={enrolling ? "Set up two-factor" : "Two-factor verification"}
        subtitle={
          enrolling
            ? "Add this account to an authenticator app (Microsoft/Google Authenticator, Authy…), then enter the 6-digit code it shows."
            : method === "backup"
            ? "Enter one of your saved backup codes."
            : "Enter the 6-digit code from your authenticator app."
        }
      />

      {enrolling && (
        <div style={{ marginBottom: 18 }}>
          {qr ? (
            <div style={{ display: "flex", justifyContent: "center", marginBottom: 12 }}>
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src={qr} alt="Scan this QR code with your authenticator app"
                   width={200} height={200}
                   style={{ border: "1px solid var(--border)", borderRadius: 10, padding: 8, background: "#fff" }} />
            </div>
          ) : (
            <div className="mute" style={{ fontSize: 12, textAlign: "center", marginBottom: 12 }}>Generating QR…</div>
          )}
          <p className="mute" style={{ fontSize: 12.5, textAlign: "center", marginBottom: 10 }}>
            Scan with your phone&apos;s authenticator app, then enter the 6-digit code below.
          </p>
          <details>
            <summary style={{ cursor: "pointer", fontSize: 12.5, color: "var(--muted)" }}>
              Can&apos;t scan? Enter a setup key
            </summary>
            <div style={{ marginTop: 8 }}>
              <div style={{
                fontFamily: "var(--font-mono)", fontSize: 15, letterSpacing: "0.06em",
                padding: "10px 12px", background: "var(--bg)",
                border: "1px solid var(--border)", borderRadius: 6, wordBreak: "break-all",
              }}>
                {groups(secret)}
              </div>
              <a href={otpauth} style={{ display: "inline-block", marginTop: 8, fontSize: 12.5, color: "var(--accent)", textDecoration: "none" }}>
                Open in an authenticator app →
              </a>
            </div>
          </details>
        </div>
      )}

      {err && <LoginError>{err}</LoginError>}

      <form onSubmit={submit}>
        <input
          value={code}
          onChange={(e) => setCode(e.target.value)}
          inputMode={method === "backup" ? "text" : "numeric"}
          autoComplete="one-time-code"
          autoFocus
          placeholder={method === "backup" ? "xxxx-xxxx" : "123456"}
          required
          style={{ ...loginInputStyle, height: 42, fontSize: 16, letterSpacing: "0.12em", textAlign: "center", fontFamily: "var(--font-mono)" }}
        />
        <button
          type="submit"
          disabled={busy}
          className="btn btn-primary"
          style={{ marginTop: 16, width: "100%", justifyContent: "center", height: 40, fontSize: 13 }}
        >
          {busy ? "Verifying…" : enrolling ? "Verify & finish setup" : "Verify"}
        </button>
      </form>

      {!enrolling && (
        <div style={{ display: "flex", flexDirection: "column", gap: 8, marginTop: 16, fontSize: 12.5 }}>
          {method !== "totp" && (
            <button className="linklike" onClick={() => { setMethod("totp"); setCode(""); setErr(null); }}
              style={linkBtn}>Use authenticator app instead</button>
          )}
          {method !== "backup" && (
            <button className="linklike" onClick={() => { setMethod("backup"); setCode(""); setErr(null); }}
              style={linkBtn}>Use a backup code</button>
          )}
        </div>
      )}
    </LoginShell>
  );
}

const linkBtn: React.CSSProperties = {
  background: "none", border: "none", padding: 0, textAlign: "left",
  color: "var(--accent)", cursor: "pointer", fontSize: 12.5,
};
