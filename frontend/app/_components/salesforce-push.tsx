"use client";

import { useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";
import { useAuth } from "./auth-provider";
import { sfPrefill, sfResolve, sfPush, type SfResolve } from "../_lib/salesforce";
import { IconUpload } from "./icons";

// Minimal shape both the PDF drawer's `Pdf` and the Files table row satisfy.
type PdfLike = {
  id: number;
  proposed_name: string | null;
  filename: string;
  first_name: string | null;
  last_name: string | null;
  committed_at: string | null;
};

const clientKey = (p: PdfLike) =>
  `${(p.last_name || "").trim().toLowerCase()}|${(p.first_name || "").trim().toLowerCase()}`;
const clientName = (p: PdfLike) =>
  [p.first_name, p.last_name].filter(Boolean).join(" ").trim() || "Unknown client";
const fileName = (p: PdfLike) => p.proposed_name || p.filename;

type PushState = "uploaded" | "duplicate" | "error";

/** Single-file "Send to Salesforce" — for the PDF drawer and each Files row. */
export function SalesforceButton({ pdf, compact = false }: { pdf: PdfLike; compact?: boolean }) {
  const { me } = useAuth();
  const [open, setOpen] = useState(false);
  if (!me?.permissions?.includes("salesforce:push")) return null;
  if (!pdf.committed_at) return null;
  return (
    <>
      <button
        className="btn"
        onClick={(e) => { e.stopPropagation(); setOpen(true); }}
        title="Send to Salesforce"
        style={compact ? { height: 28, padding: "0 9px", fontSize: 12 } : undefined}
      >
        <IconUpload /> {compact ? "SF" : "Send to Salesforce"}
      </button>
      {open && <PushDialog pdfs={[pdf]} onClose={() => setOpen(false)} />}
    </>
  );
}

/** Bulk "Send selected to Salesforce" — for the Files toolbar. */
export function SalesforceBulkButton({ pdfs, primary = false }: { pdfs: PdfLike[]; primary?: boolean }) {
  const { me } = useAuth();
  const [open, setOpen] = useState(false);
  if (!me?.permissions?.includes("salesforce:push")) return null;
  const committed = pdfs.filter((p) => p.committed_at);
  return (
    <>
      <button
        className={`btn${primary ? " btn-primary" : ""}`}
        onClick={() => setOpen(true)}
        disabled={committed.length === 0}
        title={committed.length === 0 ? "Select committed files to send" : "Send selected to Salesforce"}
      >
        <IconUpload /> Send to Salesforce{committed.length > 0 ? ` (${committed.length})` : ""}
      </button>
      {open && <PushDialog pdfs={committed} onClose={() => setOpen(false)} />}
    </>
  );
}

function PushDialog({ pdfs, onClose }: { pdfs: PdfLike[]; onClose: () => void }) {
  const groups = useMemo(() => new Set(pdfs.map(clientKey)), [pdfs]);
  const multiClient = groups.size > 1;
  const client = pdfs[0];

  const [caseNumber, setCaseNumber] = useState("");
  const [resolved, setResolved] = useState<SfResolve | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [results, setResults] = useState<Record<number, PushState> | null>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  // Prefill the case number from the learned mapping (single-client only).
  useEffect(() => {
    if (multiClient) return;
    let alive = true;
    sfPrefill(client.first_name ?? "", client.last_name ?? "")
      .then((p) => { if (alive && p.case_number) setCaseNumber(p.case_number); })
      .catch(() => {});
    return () => { alive = false; };
  }, [multiClient, client]);

  const doResolve = async () => {
    setErr(null); setResults(null); setResolved(null); setBusy(true);
    try { setResolved(await sfResolve(caseNumber.trim())); }
    catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  };

  const doPush = async () => {
    setErr(null); setBusy(true);
    const out: Record<number, PushState> = {};
    setResults({});
    for (const p of pdfs) {
      try { out[p.id] = (await sfPush(p.id, caseNumber.trim())).status; }
      catch { out[p.id] = "error"; }
      setResults({ ...out });
    }
    setBusy(false);
  };

  const finished = !!results && !busy && pdfs.every((p) => results[p.id]);

  const node = (
    <div onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }} style={overlay}>
      <div onMouseDown={(e) => e.stopPropagation()} style={modal}>
        <div style={{ fontSize: 15, fontWeight: 600, marginBottom: 3 }}>Send to Salesforce</div>

        {multiClient ? (
          <>
            <div className="mute" style={{ fontSize: 12.5, margin: "6px 0 14px" }}>
              The {pdfs.length} selected files belong to different clients. Send one client’s files at a
              time — each client has their own case number.
            </div>
            <div style={{ ...listBox, maxHeight: 170, overflow: "auto" }}>
              {[...groups].map((k) => {
                const g = pdfs.filter((p) => clientKey(p) === k);
                return (
                  <div key={k} style={{ fontSize: 12.5, padding: "3px 0" }}>
                    {clientName(g[0])} — {g.length} file(s)
                  </div>
                );
              })}
            </div>
            <div style={{ display: "flex", justifyContent: "flex-end", marginTop: 18 }}>
              <button className="btn" onClick={onClose}>Close</button>
            </div>
          </>
        ) : !results ? (
          <>
            <div className="mute" style={{ fontSize: 12.5, margin: "3px 0 16px" }}>
              {pdfs.length === 1
                ? <span className="mono">{fileName(client)}</span>
                : <>{pdfs.length} assessments for <strong>{clientName(client)}</strong></>}
            </div>

            <label style={labelStyle}>Client case number</label>
            <div style={{ display: "flex", gap: 8 }}>
              <input
                value={caseNumber}
                onChange={(e) => { setCaseNumber(e.target.value); setResolved(null); }}
                placeholder="e.g. 00039658168I"
                autoFocus
                onKeyDown={(e) => { if (e.key === "Enter" && caseNumber.trim()) doResolve(); }}
                style={inputStyle}
              />
              <button className="btn" onClick={doResolve} disabled={busy || !caseNumber.trim()}>
                {busy ? "…" : "Find"}
              </button>
            </div>

            {resolved && (
              <div style={confirmBox}>
                <div style={{ fontSize: 13 }}>
                  <span style={{ color: "var(--ok)", fontWeight: 700 }}>✓</span>{" "}
                  Case number matches <strong>{resolved.account_name}</strong>
                </div>
                <div className="mute" style={{ fontSize: 11.5, marginTop: 3 }}>
                  Sending {pdfs.length} file{pdfs.length > 1 ? "s" : ""} · their record currently has{" "}
                  {resolved.files.length}. Anything already there is skipped.
                </div>
              </div>
            )}

            {err && <div style={errBox}>{err}</div>}

            <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 18 }}>
              <button className="btn" onClick={onClose} disabled={busy}>Cancel</button>
              <button className="btn btn-primary" onClick={doPush} disabled={busy || !resolved}>
                {busy ? "Sending…" : `Send ${pdfs.length > 1 ? pdfs.length + " " : ""}to Salesforce`}
              </button>
            </div>
          </>
        ) : (
          <>
            <div className="mute" style={{ fontSize: 12, margin: "3px 0 12px" }}>
              {resolved?.account_name}
            </div>
            <div style={{ ...listBox, maxHeight: 240, overflow: "auto" }}>
              {pdfs.map((p) => {
                const st = results[p.id];
                return (
                  <div key={p.id} style={{ display: "flex", justifyContent: "space-between", gap: 12, fontSize: 12.5, padding: "4px 0" }}>
                    <span className="mono" style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                      {fileName(p)}
                    </span>
                    <span style={{ flexShrink: 0 }}>
                      {st === "uploaded" ? <span style={{ color: "var(--ok)" }}>✓ Uploaded</span>
                        : st === "duplicate" ? <span className="mute">Already there</span>
                        : st === "error" ? <span style={{ color: "var(--err)" }}>Failed</span>
                        : <span className="mute">…</span>}
                    </span>
                  </div>
                );
              })}
            </div>
            <div style={{ display: "flex", justifyContent: "flex-end", marginTop: 18 }}>
              <button className="btn btn-primary" onClick={onClose} disabled={busy}>
                {finished ? "Done" : "Sending…"}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
  // Portal to <body> so no transformed/overflow ancestor (e.g. the animated
  // .bulk-bar) can trap or clip the fixed-position modal.
  return typeof document !== "undefined" ? createPortal(node, document.body) : node;
}

const overlay: React.CSSProperties = { position: "fixed", inset: 0, background: "rgba(15,23,41,0.45)", display: "grid", placeItems: "center", zIndex: 80, padding: 16 };
const modal: React.CSSProperties = { width: "min(460px, 100%)", background: "var(--surface)", border: "1px solid var(--border-strong)", borderRadius: 8, padding: "20px 22px", boxShadow: "0 24px 60px rgba(15,23,41,0.24)" };
const labelStyle: React.CSSProperties = { display: "block", fontSize: 11.5, color: "var(--muted)", marginBottom: 5, textTransform: "uppercase", letterSpacing: "0.03em" };
const inputStyle: React.CSSProperties = { flex: 1, height: 34, padding: "0 10px", fontSize: 13, background: "var(--bg)", border: "1px solid var(--border-strong)", borderRadius: 4, color: "var(--ink)", fontFamily: "var(--font-mono)" };
const confirmBox: React.CSSProperties = { marginTop: 14, padding: "10px 12px", background: "var(--bg)", border: "1px solid var(--border)", borderRadius: 4 };
const listBox: React.CSSProperties = { padding: "8px 12px", background: "var(--bg)", border: "1px solid var(--border)", borderRadius: 4 };
const errBox: React.CSSProperties = { marginTop: 12, padding: "8px 12px", background: "var(--err-soft)", color: "var(--err)", borderRadius: 4, fontSize: 12 };
