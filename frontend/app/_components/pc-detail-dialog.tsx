"use client";

import * as Dialog from "@radix-ui/react-dialog";
import { useMemo, useState } from "react";
import { useApp, type Pdf } from "./app-provider";
import { PdfDrawer } from "./pdf-drawer";
import { PcExplorerDialog } from "./pc-explorer-dialog";
import { displayName, fmtBytes, fmtDate, fmtRelative, ftypeClass, ftypeLabel } from "./util";

type Props = {
  open: boolean;
  onClose: () => void;
  pcName: string | null;
  host: string | null;
  lastAttempt?: string | null;
  reachable?: boolean | null;
  error?: string | null;
};

function folderOf(sourcePath: string): "Desktop" | "Documents" | "Downloads" | "Other" {
  const p = sourcePath.toLowerCase();
  if (p.includes("\\desktop\\"))   return "Desktop";
  if (p.includes("\\documents\\")) return "Documents";
  if (p.includes("\\downloads\\")) return "Downloads";
  return "Other";
}

export function PcDetailDialog({ open, onClose, pcName, host, lastAttempt, reachable, error }: Props) {
  const { pdfs } = useApp();
  const [selectedPdfId, setSelectedPdfId] = useState<number | null>(null);
  const [explorerOpen, setExplorerOpen] = useState(false);

  const files = useMemo<Pdf[]>(
    () => (host ? pdfs.filter((p) => p.host === host) : []),
    [pdfs, host]
  );

  const committed = files.filter((f) => f.committed_at).length;
  const pending = files.length - committed;

  const statusLabel = reachable === null || reachable === undefined
    ? "Never scanned"
    : reachable ? "Reachable" : "Unreachable";
  const statusCls = reachable ? "pill-ok" : reachable === false ? "pill-err" : "pill-warn";

  return (
    <>
      <Dialog.Root open={open} onOpenChange={(o) => { if (!o) onClose(); }}>
        <Dialog.Portal>
          <Dialog.Overlay className="drawer-overlay" />
          <Dialog.Content className="modal" aria-describedby={undefined}>
            <div className="modal-head">
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
                  <div style={{
                    width: 36, height: 36, background: "var(--sidebar)", color: "white",
                    display: "grid", placeItems: "center", borderRadius: 4,
                    fontFamily: "var(--font-mono)", fontSize: 12, fontWeight: 700, letterSpacing: "-0.02em",
                  }}>
                    {pcName ?? "?"}
                  </div>
                  <div>
                    <Dialog.Title style={{ fontSize: 16, fontWeight: 600, letterSpacing: "-0.01em" }}>
                      {pcName} <span className="mono mute" style={{ fontSize: 12, fontWeight: 400, marginLeft: 6 }}>{host}</span>
                    </Dialog.Title>
                    <div style={{ display: "flex", alignItems: "center", gap: 10, marginTop: 4 }}>
                      <span className={`pill ${statusCls}`}>{statusLabel}</span>
                      <span className="mono mute" style={{ fontSize: 11 }}>
                        {lastAttempt ? `Last scan ${fmtRelative(lastAttempt)}` : "No scan attempt yet"}
                      </span>
                    </div>
                  </div>
                </div>
                <div style={{ display: "flex", gap: 8 }}>
                  <button className="btn" onClick={() => setExplorerOpen(true)}>Open File Explorer</button>
                  <Dialog.Close asChild><button className="btn">Close</button></Dialog.Close>
                </div>
              </div>

              {/* At-a-glance counts */}
              <div style={{ display: "flex", gap: 22, marginTop: 14, fontSize: 12 }}>
                {[
                  { k: "Files", v: files.length, color: "var(--ink)" },
                  { k: "Committed", v: committed, color: "var(--ok)" },
                  { k: "Pending", v: pending, color: pending ? "var(--warn)" : "var(--muted)" },
                ].map((s) => (
                  <div key={s.k}>
                    <div className="mute" style={{ textTransform: "uppercase", letterSpacing: "0.08em", fontSize: 10, fontWeight: 500 }}>{s.k}</div>
                    <div className="mono" style={{ fontSize: 20, fontWeight: 600, color: s.color, marginTop: 1 }}>{s.v}</div>
                  </div>
                ))}
              </div>

              {error && (
                <div style={{ marginTop: 12, padding: "8px 12px", background: "var(--err-soft)", color: "var(--err)", borderRadius: 3, fontSize: 12, fontFamily: "var(--font-mono)" }}>
                  {error}
                </div>
              )}
            </div>

            <div className="modal-body">
              {files.length === 0 ? (
                <div className="empty" style={{ padding: "50px 20px" }}>
                  <h3>No files indexed from this PC</h3>
                  <p>{reachable ? "Nothing was found in Client\\Desktop, Documents, or Downloads on the last scan." : "Run a scan to discover files."}</p>
                </div>
              ) : (
                <table className="tbl">
                  <thead>
                    <tr>
                      <th style={{ width: 50 }}></th>
                      <th style={{ width: 100 }}>Folder</th>
                      <th>Client</th>
                      <th>Assessment</th>
                      <th>Original filename</th>
                      <th style={{ textAlign: "right", width: 70 }}>Size</th>
                      <th style={{ width: 100 }}>Modified</th>
                      <th style={{ width: 100 }}>Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {files.map((f) => (
                      <tr key={f.id} onClick={() => setSelectedPdfId(f.id)} style={{ cursor: "pointer" }}>
                        <td><span className={`ftype ${ftypeClass(f.assessment_type)}`}>{ftypeLabel(f.assessment_type)}</span></td>
                        <td className="mono mute">{folderOf(f.source_path ?? "")}</td>
                        <td>{displayName(f)}</td>
                        <td className="mono mute" title={f.assessment_type ?? ""}>
                          {(f.assessment_type ?? "—").replace(/_/g, " ")}
                        </td>
                        <td className="mono" title={f.filename}>{f.filename}</td>
                        <td className="mono mute" style={{ textAlign: "right" }}>{fmtBytes(f.size)}</td>
                        <td className="mono mute">{fmtDate(f.mtime)}</td>
                        <td>
                          {f.committed_at
                            ? <span className="pill pill-ok" title={`Copied to ${f.dest_path ?? ""}`}>Committed</span>
                            : <span className="pill pill-warn">Pending</span>}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>

            <div className="modal-foot">
              Click any row to open the PDF viewer. Committed files stream from the network share, pending files from the source PC.
            </div>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>

      {/* Nested PDF viewer on top of the PC detail modal */}
      <PdfDrawer
        pdf={selectedPdfId ? (files.find((f) => f.id === selectedPdfId) ?? null) : null}
        open={selectedPdfId !== null}
        onClose={() => setSelectedPdfId(null)}
      />

      {/* Nested File Explorer */}
      <PcExplorerDialog
        open={explorerOpen}
        onClose={() => setExplorerOpen(false)}
        pcName={pcName}
        host={host}
      />
    </>
  );
}
