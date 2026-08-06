"use client";

import * as Dialog from "@radix-ui/react-dialog";
import { useCallback, useEffect, useRef, useState } from "react";
import { IconRefresh, IconUpload } from "./icons";
import { apiFetch } from "../_lib/auth";

// ponytail: same-origin via next.config rewrite; use apiFetch for auth headers.
const API = "";
const ROOTS = ["Desktop", "Documents", "Downloads"] as const;
type Root = typeof ROOTS[number];

type Entry = { name: string; is_dir: boolean; size: number | null; mtime: string };

function fmtBytes(n: number | null): string {
  if (n === null) return "";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 ** 3) return `${(n / 1024 / 1024).toFixed(1)} MB`;
  return `${(n / 1024 ** 3).toFixed(2)} GB`;
}

function fmtDate(iso: string): string {
  const d = new Date(iso.includes("Z") ? iso : iso + "Z");
  return d.toLocaleString(undefined, { month: "short", day: "2-digit", hour: "2-digit", minute: "2-digit" });
}

type Props = {
  open: boolean;
  onClose: () => void;
  pcName: string | null;
  host: string | null;
};

export function PcExplorerDialog({ open, onClose, pcName, host }: Props) {
  const [root, setRoot] = useState<Root>("Desktop");
  const [subs, setSubs] = useState<string[]>([]);       // path segments beyond root
  const [entries, setEntries] = useState<Entry[]>([]);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [renaming, setRenaming] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const [confirmDelete, setConfirmDelete] = useState<Entry | null>(null);
  const [showMkdir, setShowMkdir] = useState(false);
  const [mkdirValue, setMkdirValue] = useState("");
  const [uploading, setUploading] = useState(false);
  const [uploadErr, setUploadErr] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const currentPath = [root, ...subs].join("/");

  const load = useCallback(async () => {
    if (!pcName) return;
    setLoading(true);
    setErr(null);
    try {
      const r = await apiFetch(`${API}/api/pcs/${pcName}/browse?path=${encodeURIComponent(currentPath)}`);
      if (!r.ok) {
        const j = await r.json().catch(() => ({}));
        throw new Error(j.detail ?? `HTTP ${r.status}`);
      }
      const j = await r.json();
      setEntries(j.entries || []);
    } catch (e) {
      setErr(String(e instanceof Error ? e.message : e));
      setEntries([]);
    } finally {
      setLoading(false);
    }
  }, [pcName, currentPath]);

  useEffect(() => {
    if (open && pcName) load();
  }, [open, pcName, load]);

  // Reset when pc changes / dialog closes
  useEffect(() => {
    if (!open) {
      setSubs([]);
      setRoot("Desktop");
      setRenaming(null);
      setConfirmDelete(null);
      setShowMkdir(false);
      setMkdirValue("");
      setUploadErr(null);
    }
  }, [open, pcName]);

  const enterDir = (name: string) => setSubs((s) => [...s, name]);
  const goCrumb = (i: number) => {
    // i = -1 → root itself, i = 0..subs.length-1 → that sub
    if (i < 0) setSubs([]);
    else setSubs((s) => s.slice(0, i + 1));
  };
  const switchRoot = (r: Root) => { setRoot(r); setSubs([]); };

  // ponytail: <a href> can't send Authorization; fetch + blob + click.
  const doDownload = async (name: string) => {
    const path = [currentPath, name].join("/");
    const r = await apiFetch(`${API}/api/pcs/${pcName}/file?path=${encodeURIComponent(path)}`);
    if (!r.ok) { setErr(`download failed: HTTP ${r.status}`); return; }
    const blob = await r.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = name;
    document.body.appendChild(a); a.click(); a.remove();
    URL.revokeObjectURL(url);
  };

  const doRename = async (oldName: string, newName: string) => {
    if (!pcName || !newName || newName === oldName) { setRenaming(null); return; }
    try {
      const r = await apiFetch(`${API}/api/pcs/${pcName}/rename?path=${encodeURIComponent([currentPath, oldName].join("/"))}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ new_name: newName }),
      });
      if (!r.ok) {
        const j = await r.json().catch(() => ({}));
        throw new Error(j.detail ?? `HTTP ${r.status}`);
      }
      setRenaming(null);
      load();
    } catch (e) {
      setErr(String(e instanceof Error ? e.message : e));
    }
  };

  const doDelete = async () => {
    if (!pcName || !confirmDelete) return;
    try {
      const r = await apiFetch(`${API}/api/pcs/${pcName}/delete?path=${encodeURIComponent([currentPath, confirmDelete.name].join("/"))}`, {
        method: "DELETE",
      });
      if (!r.ok) {
        const j = await r.json().catch(() => ({}));
        throw new Error(j.detail ?? `HTTP ${r.status}`);
      }
      setConfirmDelete(null);
      load();
    } catch (e) {
      setErr(String(e instanceof Error ? e.message : e));
      setConfirmDelete(null);
    }
  };

  const doMkdir = async () => {
    if (!pcName || !mkdirValue.trim()) return;
    try {
      const r = await apiFetch(`${API}/api/pcs/${pcName}/mkdir?path=${encodeURIComponent(currentPath)}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: mkdirValue.trim() }),
      });
      if (!r.ok) {
        const j = await r.json().catch(() => ({}));
        throw new Error(j.detail ?? `HTTP ${r.status}`);
      }
      setShowMkdir(false);
      setMkdirValue("");
      load();
    } catch (e) {
      setErr(String(e instanceof Error ? e.message : e));
    }
  };

  const doUpload = async (files: FileList | null) => {
    if (!pcName || !files || files.length === 0) return;
    setUploading(true);
    setUploadErr(null);
    try {
      for (const file of Array.from(files)) {
        const fd = new FormData();
        fd.append("file", file);
        const r = await apiFetch(`${API}/api/pcs/${pcName}/upload?path=${encodeURIComponent(currentPath)}`, {
          method: "POST",
          body: fd,
        });
        if (!r.ok) {
          const j = await r.json().catch(() => ({}));
          throw new Error(`${file.name}: ${j.detail ?? `HTTP ${r.status}`}`);
        }
      }
      load();
    } catch (e) {
      setUploadErr(String(e instanceof Error ? e.message : e));
    } finally {
      setUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  };

  return (
    <Dialog.Root open={open} onOpenChange={(o) => { if (!o) onClose(); }}>
      <Dialog.Portal>
        <Dialog.Overlay className="drawer-overlay" />
        <Dialog.Content className="modal" style={{ width: "min(1100px, 94vw)", height: "82vh", maxHeight: "82vh" }} aria-describedby={undefined}>
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
                  <Dialog.Title style={{ fontSize: 15, fontWeight: 600 }}>
                    {pcName} <span className="mono mute" style={{ fontSize: 12, fontWeight: 400, marginLeft: 6 }}>{host}</span>
                  </Dialog.Title>
                  <div className="mute" style={{ fontSize: 11, marginTop: 2 }}>
                    File Explorer · <span className="mono">C:\Users\Client\</span>
                  </div>
                </div>
              </div>
              <Dialog.Close asChild><button className="btn">Close</button></Dialog.Close>
            </div>

            {/* Root tabs */}
            <div className="chips" style={{ marginTop: 14 }}>
              {ROOTS.map((r) => (
                <button key={r} className={`chip${root === r ? " active" : ""}`} onClick={() => switchRoot(r)}>{r}</button>
              ))}
            </div>
          </div>

          {/* Toolbar: breadcrumb + actions */}
          <div className="toolbar" style={{ padding: "10px 22px" }}>
            <div style={{ display: "flex", alignItems: "center", gap: 4, flexWrap: "wrap", flex: 1, minWidth: 0 }} className="mono">
              <button className="chip" style={{ background: "transparent", border: "none", padding: "2px 6px", color: "var(--accent)" }} onClick={() => goCrumb(-1)}>{root}</button>
              {subs.map((s, i) => (
                <span key={i} style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
                  <span className="mute">›</span>
                  <button className="chip" style={{ background: "transparent", border: "none", padding: "2px 6px", color: i === subs.length - 1 ? "var(--ink)" : "var(--accent)" }} onClick={() => goCrumb(i)}>{s}</button>
                </span>
              ))}
            </div>
            <button className="btn" onClick={() => setShowMkdir(true)} title="New folder">+ Folder</button>
            <button className="btn" onClick={() => fileInputRef.current?.click()} disabled={uploading}>
              <IconUpload /> {uploading ? "Uploading…" : "Upload"}
            </button>
            <input ref={fileInputRef} type="file" multiple onChange={(e) => doUpload(e.target.files)} style={{ display: "none" }} />
            <button className="btn" onClick={load} title="Refresh"><IconRefresh /></button>
          </div>

          {uploadErr && (
            <div style={{ margin: "0 22px", padding: "6px 10px", background: "var(--err-soft)", color: "var(--err)", borderRadius: 3, fontSize: 12, fontFamily: "var(--font-mono)" }}>
              {uploadErr}
            </div>
          )}

          <div className="modal-body">
            {loading ? (
              <div className="empty"><p>Loading…</p></div>
            ) : err ? (
              <div className="empty">
                <h3 style={{ color: "var(--err)" }}>Could not load folder</h3>
                <p className="mono" style={{ fontSize: 12 }}>{err}</p>
              </div>
            ) : entries.length === 0 ? (
              <div className="empty">
                <h3>Empty folder</h3>
                <p>Nothing in this directory.</p>
              </div>
            ) : (
              <table className="tbl">
                <thead>
                  <tr>
                    <th style={{ width: 36 }}></th>
                    <th>Name</th>
                    <th style={{ width: 100, textAlign: "right" }}>Size</th>
                    <th style={{ width: 140 }}>Modified</th>
                    <th style={{ width: 180 }}></th>
                  </tr>
                </thead>
                <tbody>
                  {entries.map((e) => {
                    const isRenaming = renaming === e.name;
                    return (
                      <tr key={e.name} style={{ cursor: e.is_dir ? "pointer" : "default" }}>
                        <td>
                          {e.is_dir ? (
                            <span title="Folder" style={{ fontSize: 16 }}>📁</span>
                          ) : (
                            <span title="File" style={{ fontSize: 16 }}>📄</span>
                          )}
                        </td>
                        <td onClick={() => { if (e.is_dir && !isRenaming) enterDir(e.name); }}>
                          {isRenaming ? (
                            <input
                              autoFocus
                              value={renameValue}
                              onChange={(ev) => setRenameValue(ev.target.value)}
                              onKeyDown={(ev) => {
                                if (ev.key === "Enter") doRename(e.name, renameValue);
                                if (ev.key === "Escape") setRenaming(null);
                              }}
                              onBlur={() => doRename(e.name, renameValue)}
                              style={{ width: "100%", height: 26, padding: "0 6px", fontSize: 13, background: "var(--surface)", border: "1px solid var(--accent)", borderRadius: 3, color: "var(--ink)", fontFamily: e.is_dir ? "var(--font-sans)" : "var(--font-mono)" }}
                            />
                          ) : (
                            <span className={e.is_dir ? "" : "mono"} style={{ fontSize: e.is_dir ? 13 : 12, fontWeight: e.is_dir ? 500 : 400 }}>{e.name}</span>
                          )}
                        </td>
                        <td className="mono mute" style={{ textAlign: "right" }}>{fmtBytes(e.size)}</td>
                        <td className="mono mute">{fmtDate(e.mtime)}</td>
                        <td style={{ textAlign: "right" }}>
                          <div style={{ display: "flex", gap: 4, justifyContent: "flex-end" }}>
                            {!e.is_dir && (
                              <button className="btn" style={{ height: 26, padding: "0 8px", fontSize: 11 }} onClick={() => doDownload(e.name)}>Download</button>
                            )}
                            <button className="btn" style={{ height: 26, padding: "0 8px", fontSize: 11 }} onClick={() => { setRenaming(e.name); setRenameValue(e.name); }}>Rename</button>
                            <button className="btn" style={{ height: 26, padding: "0 8px", fontSize: 11, color: "var(--err)", borderColor: "var(--err)" }} onClick={() => setConfirmDelete(e)}>Delete</button>
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            )}
          </div>

          <div className="modal-foot" style={{ display: "flex", justifyContent: "space-between" }}>
            <span>{entries.length} item{entries.length === 1 ? "" : "s"}</span>
            <span className="mono">{[root, ...subs].join("\\")}</span>
          </div>
        </Dialog.Content>
      </Dialog.Portal>

      {/* Mkdir prompt */}
      <Dialog.Root open={showMkdir} onOpenChange={setShowMkdir}>
        <Dialog.Portal>
          <Dialog.Overlay className="drawer-overlay" />
          <Dialog.Content className="modal" style={{ width: "min(420px, 92vw)" }} aria-describedby={undefined}>
            <div className="modal-head">
              <Dialog.Title style={{ fontSize: 15, fontWeight: 600 }}>New folder</Dialog.Title>
              <div className="mute" style={{ fontSize: 12, marginTop: 4 }}>in <span className="mono">{[root, ...subs].join("\\")}</span></div>
            </div>
            <div className="modal-body" style={{ padding: "16px 22px" }}>
              <input
                autoFocus
                value={mkdirValue}
                onChange={(e) => setMkdirValue(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter") doMkdir(); if (e.key === "Escape") setShowMkdir(false); }}
                placeholder="Folder name"
                style={{ width: "100%", height: 34, padding: "0 12px", fontSize: 13, background: "var(--surface)", border: "1px solid var(--border-strong)", borderRadius: 3, color: "var(--ink)" }}
              />
            </div>
            <div className="modal-foot" style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
              <Dialog.Close asChild><button className="btn">Cancel</button></Dialog.Close>
              <button className="btn btn-primary" onClick={doMkdir} disabled={!mkdirValue.trim()}>Create</button>
            </div>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>

      {/* Delete confirm */}
      <Dialog.Root open={!!confirmDelete} onOpenChange={(o) => { if (!o) setConfirmDelete(null); }}>
        <Dialog.Portal>
          <Dialog.Overlay className="drawer-overlay" />
          <Dialog.Content className="modal" style={{ width: "min(460px, 92vw)" }} aria-describedby={undefined}>
            <div className="modal-head">
              <Dialog.Title style={{ fontSize: 15, fontWeight: 600 }}>Delete {confirmDelete?.is_dir ? "folder" : "file"}?</Dialog.Title>
              <div className="mute" style={{ fontSize: 12, marginTop: 4 }}>
                <span className="mono">{confirmDelete?.name}</span>
                {confirmDelete?.is_dir && <> — folder must be empty</>}
              </div>
            </div>
            <div className="modal-foot" style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
              <Dialog.Close asChild><button className="btn">Cancel</button></Dialog.Close>
              <button className="btn" style={{ background: "var(--err)", color: "white", borderColor: "var(--err)" }} onClick={doDelete}>Delete</button>
            </div>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
    </Dialog.Root>
  );
}
