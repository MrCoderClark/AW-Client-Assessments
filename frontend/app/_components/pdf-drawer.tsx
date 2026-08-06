"use client";

import * as Dialog from "@radix-ui/react-dialog";
import { useEffect, useState } from "react";
import { useApp, type Pdf } from "./app-provider";
import { displayName, ftypeClass, ftypeLabel } from "./util";
import { apiFetch } from "../_lib/auth";

// ponytail: same-origin via next.config rewrite; use apiFetch for auth headers.
const API = "";

function buildProposed(assessment: string | null, first: string, last: string): string {
  if (!assessment) return "—";
  const cap = (s: string) => s.trim() ? s.trim().charAt(0).toUpperCase() + s.trim().slice(1).toLowerCase() : "";
  const F = cap(first), L = cap(last);
  const name = F && L ? `${F}_${L}` : "Unknown-Client";
  return `${assessment}-${name}.pdf`;
}

/** Reusable viewer content: metadata + edit form + iframe. Not wrapped in a Dialog. */
export function PdfPanel({ pdf, onClose, showClose = true, titleEl }: {
  pdf: Pdf;
  onClose?: () => void;
  showClose?: boolean;
  titleEl?: React.ComponentType<{ children: React.ReactNode; style?: React.CSSProperties }>;
}) {
  const { refresh, runRestore } = useApp();
  const [editing, setEditing] = useState(false);
  const [first, setFirst] = useState(pdf.first_name ?? "");
  const [last, setLast] = useState(pdf.last_name ?? "");
  const [saving, setSaving] = useState(false);
  const [restoring, setRestoring] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [viewerBust, setViewerBust] = useState(0);
  const [blobUrl, setBlobUrl] = useState<string | null>(null);
  const isArchived = !!pdf.archived_at;

  useEffect(() => {
    setEditing(false);
    setErr(null);
    setFirst(pdf.first_name ?? "");
    setLast(pdf.last_name ?? "");
  }, [pdf.id]);

  // ponytail: iframe/anchor can't send Authorization; fetch bytes and use a
  // blob URL. Revoked on unmount / pdf change to avoid leaking object URLs.
  useEffect(() => {
    let alive = true;
    let currentUrl: string | null = null;
    (async () => {
      const r = await apiFetch(`/api/pdfs/${pdf.id}/content`);
      if (!alive || !r.ok) return;
      const blob = await r.blob();
      if (!alive) return;
      currentUrl = URL.createObjectURL(blob);
      setBlobUrl(currentUrl);
    })();
    return () => {
      alive = false;
      if (currentUrl) URL.revokeObjectURL(currentUrl);
      setBlobUrl(null);
    };
  }, [pdf.id, viewerBust]);

  const doDownload = () => {
    if (!blobUrl) return;
    const a = document.createElement("a");
    a.href = blobUrl;
    a.download = pdf.proposed_name || pdf.filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
  };

  const save = async () => {
    setSaving(true);
    setErr(null);
    try {
      const r = await apiFetch(`${API}/api/pdfs/${pdf.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ first_name: first, last_name: last }),
      });
      if (!r.ok) {
        const body = await r.json().catch(() => ({}));
        throw new Error(body.detail ?? `HTTP ${r.status}`);
      }
      await refresh();
      setEditing(false);
      setViewerBust((n) => n + 1);
    } catch (e) {
      setErr(String(e instanceof Error ? e.message : e));
    } finally {
      setSaving(false);
    }
  };

  const proposedPreview = buildProposed(pdf.assessment_type, first, last);
  const canSave = !saving && (first !== (pdf.first_name ?? "") || last !== (pdf.last_name ?? ""));

  const Title = titleEl ?? (({ children, style }) => <div style={style}>{children}</div>);

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", background: "var(--surface)" }}>
      <div style={{ padding: "14px 18px", borderBottom: "1px solid var(--border)", background: "var(--surface)" }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 8 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10, minWidth: 0 }}>
            <span className={`ftype ${ftypeClass(pdf.assessment_type)}`}>{ftypeLabel(pdf.assessment_type)}</span>
            <div style={{ minWidth: 0 }}>
              <Title style={{ fontSize: 14, fontWeight: 600, letterSpacing: "-0.01em", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                {displayName(pdf)}
              </Title>
              <div className="mono mute" style={{ fontSize: 11 }}>
                {(pdf.assessment_type ?? "—").replace(/_/g, " ")}
              </div>
            </div>
          </div>
          <div style={{ display: "flex", gap: 8, flexShrink: 0 }}>
            {!editing && !isArchived && <button className="btn" onClick={() => setEditing(true)}>Edit client</button>}
            {isArchived && (
              <button className="btn" disabled={restoring} onClick={async () => {
                setRestoring(true);
                try { await runRestore([pdf.id]); onClose?.(); } finally { setRestoring(false); }
              }}>{restoring ? "Restoring…" : "Restore"}</button>
            )}
            <button className="btn" onClick={doDownload} disabled={!blobUrl}>Download</button>
            {showClose && onClose && <button className="btn" onClick={onClose}>Close</button>}
          </div>
        </div>

        {isArchived && (
          <div style={{
            marginTop: 8, padding: "8px 12px",
            background: "var(--surface-2)", border: "1px solid var(--border-strong)",
            borderRadius: 3, fontSize: 12,
            display: "flex", alignItems: "center", gap: 8,
          }}>
            <span style={{ fontWeight: 600 }}>Archived</span>
            <span className="mute">
              {pdf.archived_at && `on ${new Date(pdf.archived_at).toLocaleString()}`}
              {pdf.archive_status === "lost" && " · file marked lost — see repair panel"}
            </span>
            <span className="mono mute" style={{ marginLeft: "auto", fontSize: 11 }}>
              Restore returns it to its original folder.
            </span>
          </div>
        )}

        {editing && (
          <div style={{ background: "var(--bg)", border: "1px solid var(--border-strong)", borderRadius: 3, padding: "12px 14px", marginTop: 6, marginBottom: 8 }}>
            <div style={{ display: "flex", gap: 10, alignItems: "flex-end", flexWrap: "wrap" }}>
              <div style={{ flex: "1 1 180px" }}>
                <label style={{ fontSize: 10.5, fontWeight: 500, color: "var(--muted)", textTransform: "uppercase", letterSpacing: "0.06em", display: "block", marginBottom: 4 }}>First name</label>
                <input value={first} onChange={(e) => setFirst(e.target.value)} disabled={saving}
                       style={{ width: "100%", height: 30, padding: "0 10px", fontSize: 13, background: "var(--surface)", border: "1px solid var(--border-strong)", borderRadius: 3, color: "var(--ink)", fontFamily: "var(--font-sans)" }} />
              </div>
              <div style={{ flex: "1 1 180px" }}>
                <label style={{ fontSize: 10.5, fontWeight: 500, color: "var(--muted)", textTransform: "uppercase", letterSpacing: "0.06em", display: "block", marginBottom: 4 }}>Last name</label>
                <input value={last} onChange={(e) => setLast(e.target.value)} disabled={saving}
                       style={{ width: "100%", height: 30, padding: "0 10px", fontSize: 13, background: "var(--surface)", border: "1px solid var(--border-strong)", borderRadius: 3, color: "var(--ink)", fontFamily: "var(--font-sans)" }} />
              </div>
              <div style={{ display: "flex", gap: 6 }}>
                <button className="btn" onClick={() => { setEditing(false); setErr(null); setFirst(pdf.first_name ?? ""); setLast(pdf.last_name ?? ""); }} disabled={saving}>Cancel</button>
                <button className="btn btn-primary" onClick={save} disabled={!canSave}>{saving ? "Saving…" : "Save & Rename"}</button>
              </div>
            </div>
            <div className="mono mute" style={{ fontSize: 11.5, marginTop: 10 }}>
              Will rename to: <span style={{ color: "var(--ink)" }}>{proposedPreview}</span>
              {pdf.dest_path && <> · on <span style={{ color: "var(--ink)" }}>{pdf.dest_path.slice(0, pdf.dest_path.lastIndexOf("\\"))}</span></>}
            </div>
            {err && (
              <div style={{ marginTop: 8, padding: "6px 10px", background: "var(--err-soft)", color: "var(--err)", borderRadius: 3, fontSize: 12, fontFamily: "var(--font-mono)" }}>
                {err}
              </div>
            )}
          </div>
        )}

      </div>
      <div style={{ flex: 1, background: "#525659", minHeight: 0 }}>
        {blobUrl ? (
          <iframe
            key={`${pdf.id}-${viewerBust}`}
            src={`${blobUrl}#toolbar=1&navpanes=0`}
            style={{ width: "100%", height: "100%", border: 0, display: "block" }}
            title={displayName(pdf)}
          />
        ) : (
          <div style={{ height: "100%", display: "grid", placeItems: "center", color: "rgba(255,255,255,0.6)", fontSize: 12 }}>
            Loading…
          </div>
        )}
      </div>
    </div>
  );
}

/** Modal drawer wrapper — right-side Dialog. Used by Dashboard and PC-detail. */
export function PdfDrawer({ pdf, open, onClose }: { pdf: Pdf | null; open: boolean; onClose: () => void }) {
  return (
    <Dialog.Root open={open} onOpenChange={(o) => { if (!o) onClose(); }}>
      <Dialog.Portal>
        <Dialog.Overlay className="drawer-overlay" />
        <Dialog.Content className="drawer" style={{ width: "min(1100px, 92vw)", padding: 0 }}>
          {pdf ? <PdfPanel pdf={pdf} onClose={onClose} titleEl={Dialog.Title as never} /> : null}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

/** Placeholder shown on Files when nothing is selected. */
export function PdfPanelEmpty() {
  return (
    <div style={{ display: "grid", placeItems: "center", height: "100%", background: "var(--surface)", color: "var(--muted)", textAlign: "center", padding: 40 }}>
      <div>
        <div style={{ fontSize: 34, marginBottom: 8, opacity: 0.35 }}>◨</div>
        <div style={{ fontSize: 13, fontWeight: 500, color: "var(--ink)" }}>Select a file</div>
        <div style={{ fontSize: 12, marginTop: 3 }}>Pick any row on the left to preview it here.</div>
      </div>
    </div>
  );
}
