"use client";

import * as Dialog from "@radix-ui/react-dialog";
import { useEffect, useMemo, useState } from "react";
import { useApp, type Pdf } from "../_components/app-provider";
import { useAuth } from "../_components/auth-provider";
import { IconSearch } from "../_components/icons";
import { PdfPanel, PdfPanelEmpty } from "../_components/pdf-drawer";
import { apiFetch } from "../_lib/auth";
import { displayName, fmtBytes, fmtDate, ftypeClass, ftypeLabel } from "../_components/util";

type ArchivedView = "false" | "true" | "all";

type SortKey = "indexed_at" | "mtime" | "size" | "name" | "host";
type Filter = "all" | "pending" | "committed";
const PAGE_SIZES = [25, 50, 100] as const;

function toCSV(rows: ReturnType<typeof useApp>["pdfs"]): string {
  const header = ["id","host","assessment_type","first_name","last_name","filename","proposed_name","size","mtime","indexed_at","committed_at","dest_path","source_path"];
  const esc = (v: unknown) => {
    if (v === null || v === undefined) return "";
    const s = String(v);
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  const lines = [header.join(",")];
  for (const r of rows) {
    lines.push(header.map((k) => esc((r as unknown as Record<string, unknown>)[k])).join(","));
  }
  return lines.join("\n");
}

function downloadCsv(csv: string, filename: string) {
  const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = filename; a.click();
  URL.revokeObjectURL(url);
}

export default function FilesPage() {
  const { pdfs: activePdfs, loading: activeLoading, runBulk, running, runArchive, runRestore, mutationVersion } = useApp();
  const { me } = useAuth();
  const canArchive = (me?.permissions ?? []).includes("pdf:archive");

  const [archivedView, setArchivedView] = useState<ArchivedView>("false");
  const [viewPdfs, setViewPdfs] = useState<Pdf[]>([]);
  const [viewLoading, setViewLoading] = useState(false);

  // Provider owns the active list (dashboard reads it too). Only fetch a
  // separate list here when the user opts into an archived-inclusive view.
  useEffect(() => {
    if (archivedView === "false") { setViewPdfs([]); return; }
    setViewLoading(true);
    let alive = true;
    (async () => {
      try {
        const r = await apiFetch(`/api/pdfs?archived=${archivedView}`);
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const data = await r.json();
        if (alive) setViewPdfs(data);
      } catch {
        if (alive) setViewPdfs([]);
      } finally {
        if (alive) setViewLoading(false);
      }
    })();
    return () => { alive = false; };
  }, [archivedView, mutationVersion]);

  const pdfs = archivedView === "false" ? activePdfs : viewPdfs;
  const loading = archivedView === "false" ? activeLoading : viewLoading;

  const [q, setQ] = useState("");
  const [filter, setFilter] = useState<Filter>("all");
  const [sortKey, setSortKey] = useState<SortKey>("indexed_at");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [checked, setChecked] = useState<Set<number>>(new Set());
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleteFiles, setDeleteFiles] = useState(false);
  const [pageSize, setPageSize] = useState<number>(25);
  const [page, setPage] = useState(1);

  // Open a specific PDF via ?open=<id> (from command palette).
  // ponytail: read raw location on mount to skip the useSearchParams Suspense dance.
  useEffect(() => {
    const id = new URLSearchParams(window.location.search).get("open");
    if (id) setSelectedId(parseInt(id, 10));
  }, []);

  const rows = useMemo(() => {
    const term = q.trim().toLowerCase();
    let list = pdfs;
    if (filter === "committed") list = list.filter((p) => p.committed_at);
    else if (filter === "pending") list = list.filter((p) => !p.committed_at);
    if (term) {
      list = list.filter((p) => {
        // Search matches what you SEE — the committed on-share basename for
        // committed rows, the source filename for pending ones. Historical
        // source names are visible on hover but don't contribute to search.
        const shownName = p.dest_path?.split("\\").pop() || p.filename;
        const bag = [shownName, p.host, p.first_name, p.last_name, p.assessment_type, p.proposed_name]
          .filter(Boolean).join(" ").toLowerCase();
        return bag.includes(term);
      });
    }
    const dir = sortDir === "asc" ? 1 : -1;
    return [...list].sort((a, b) => {
      let av: string | number = ""; let bv: string | number = "";
      switch (sortKey) {
        case "name":  av = displayName(a).toLowerCase(); bv = displayName(b).toLowerCase(); break;
        case "host":  av = a.host; bv = b.host; break;
        case "size":  av = a.size; bv = b.size; break;
        case "mtime": av = a.mtime; bv = b.mtime; break;
        default:      av = a.indexed_at; bv = b.indexed_at;
      }
      return av < bv ? -1 * dir : av > bv ? 1 * dir : 0;
    });
  }, [pdfs, q, filter, sortKey, sortDir]);

  // Reset to page 1 whenever the visible slice changes shape.
  useEffect(() => { setPage(1); }, [q, filter, sortKey, sortDir, pageSize]);

  const totalPages = Math.max(1, Math.ceil(rows.length / pageSize));
  const clampedPage = Math.min(page, totalPages);
  const startIdx = (clampedPage - 1) * pageSize;
  const pageRows = rows.slice(startIdx, startIdx + pageSize);

  const toggleSort = (k: SortKey) => {
    if (k === sortKey) setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    else { setSortKey(k); setSortDir("desc"); }
  };
  const arrow = (k: SortKey) => sortKey === k ? <span className="arrow">{sortDir === "asc" ? "↑" : "↓"}</span> : null;
  const thCls = (k: SortKey) => sortKey === k ? "sort-active" : "";

  const selectedPdf = selectedId ? pdfs.find((p) => p.id === selectedId) : null;
  const split = selectedId !== null;

  // Selection helpers — "select all" applies to the current page.
  const visibleIds = pageRows.map((r) => r.id);
  const allVisibleChecked = visibleIds.length > 0 && visibleIds.every((id) => checked.has(id));
  const someVisibleChecked = visibleIds.some((id) => checked.has(id)) && !allVisibleChecked;
  const toggleRow = (id: number) => {
    setChecked((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  };
  const toggleAll = () => {
    setChecked((prev) => {
      const next = new Set(prev);
      if (allVisibleChecked) visibleIds.forEach((id) => next.delete(id));
      else visibleIds.forEach((id) => next.add(id));
      return next;
    });
  };
  const clearSelection = () => setChecked(new Set());

  const selectedRows = pdfs.filter((p) => checked.has(p.id));
  const selectedPending = selectedRows.filter((p) => !p.committed_at).length;

  const doCommit = () => {
    if (!checked.size) return;
    runBulk("commit", [...checked]);
    clearSelection();
  };
  const doDelete = () => {
    runBulk("delete", [...checked], { deleteFiles });
    setConfirmDelete(false);
    setDeleteFiles(false);
    clearSelection();
  };
  const doExport = () => {
    downloadCsv(toCSV(selectedRows), `client-files-${new Date().toISOString().slice(0,10)}.csv`);
  };

  const doArchive = async () => {
    if (!checked.size) return;
    const ids = [...checked];
    const res = await runArchive(ids);
    if (res.failed) alert(`Archived ${res.ok}/${ids.length}. ${res.failed} failed — see the admin archive page.`);
    clearSelection();
  };
  const doRestore = async () => {
    if (!checked.size) return;
    const ids = [...checked];
    const res = await runRestore(ids);
    if (res.failed) alert(`Restored ${res.ok}/${ids.length}. ${res.failed} failed — see the admin archive page.`);
    clearSelection();
  };

  const isArchivedView = archivedView === "true";
  const isMixedView = archivedView === "all";

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "calc(100vh - 60px)" }}>
      <div className="toolbar">
        <div className="search">
          <span className="search-icon"><IconSearch /></span>
          <input placeholder="Search by client, filename, PC, assessment…" value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
        {canArchive && (
          <div className="chips">
            <button className={`chip${archivedView === "false" ? " active" : ""}`} onClick={() => setArchivedView("false")}>Active</button>
            <button className={`chip${archivedView === "true"  ? " active" : ""}`} onClick={() => setArchivedView("true")}>Archived</button>
            <button className={`chip${archivedView === "all"   ? " active" : ""}`} onClick={() => setArchivedView("all")}>All</button>
          </div>
        )}
        {!isArchivedView && (
          <div className="chips">
            <button className={`chip${filter === "all" ? " active" : ""}`}       onClick={() => setFilter("all")}>All ({pdfs.length})</button>
            <button className={`chip${filter === "pending" ? " active" : ""}`}   onClick={() => setFilter("pending")}>Pending ({pdfs.filter(p => !p.committed_at).length})</button>
            <button className={`chip${filter === "committed" ? " active" : ""}`} onClick={() => setFilter("committed")}>Committed ({pdfs.filter(p => p.committed_at).length})</button>
          </div>
        )}
        <div className="spacer" />
        <button
          className="btn"
          onClick={() => downloadCsv(toCSV(rows), `client-files-${new Date().toISOString().slice(0,10)}.csv`)}
          disabled={rows.length === 0}
          title="Export the currently-filtered rows as CSV"
        >
          Export view
        </button>
        <label className="mono mute" style={{ fontSize: 11, display: "flex", alignItems: "center", gap: 6 }}>
          Per page
          <select
            value={pageSize}
            onChange={(e) => setPageSize(Number(e.target.value))}
            style={{ height: 26, padding: "0 6px", fontSize: 12, background: "var(--surface)", border: "1px solid var(--border-strong)", borderRadius: 3, color: "var(--ink)" }}
          >
            {PAGE_SIZES.map((n) => <option key={n} value={n}>{n}</option>)}
          </select>
        </label>
        <div className="mono mute" style={{ fontSize: 11 }}>{rows.length} match{rows.length === 1 ? "" : "es"}</div>
      </div>

      <div style={{ flex: 1, display: "flex", minHeight: 0, background: "var(--bg)" }}>
        <div style={{
          flex: split ? "0 0 46%" : "1 1 auto",
          minWidth: 0,
          padding: split ? "16px 0 16px 20px" : "20px 28px",
          transition: "flex-basis 180ms ease",
          overflow: "hidden",
          display: "flex",
        }}>
          <div className="card" style={{ overflow: "hidden", flex: 1, display: "flex", flexDirection: "column", minHeight: 0 }}>
            {loading ? (
              <div className="empty"><p>Loading…</p></div>
            ) : rows.length === 0 ? (
              <div className="empty">
                <h3>No files match</h3>
                <p>{pdfs.length === 0 ? "Run a scan to discover PDFs." : "Try clearing search or filter."}</p>
              </div>
            ) : (
              <>
                <div style={{ overflow: "auto", flex: 1, minHeight: 0 }}>
                  <table className="tbl">
                    <thead>
                      <tr>
                        <th className="check">
                          <input
                            type="checkbox"
                            checked={allVisibleChecked}
                            ref={(el) => { if (el) el.indeterminate = someVisibleChecked; }}
                            onChange={toggleAll}
                            aria-label="Select all"
                          />
                        </th>
                        <th style={{ width: 56 }}></th>
                        <th className={thCls("name")} onClick={() => toggleSort("name")}>Client {arrow("name")}</th>
                        {!split && <th>Assessment</th>}
                        <th className={thCls("host")} onClick={() => toggleSort("host")}>PC {arrow("host")}</th>
                        {!split && <th>Filename</th>}
                        {!split && <th style={{ width: 80, textAlign: "right" }} className={thCls("size")} onClick={() => toggleSort("size")}>Size {arrow("size")}</th>}
                        {!split && <th style={{ width: 110 }} className={thCls("mtime")} onClick={() => toggleSort("mtime")}>Modified {arrow("mtime")}</th>}
                        <th style={{ width: 110 }} className={thCls("indexed_at")} onClick={() => toggleSort("indexed_at")}>Indexed {arrow("indexed_at")}</th>
                        <th style={{ width: 100 }}>Status</th>
                      </tr>
                    </thead>
                    <tbody>
                      {pageRows.map((p) => {
                        const isChecked = checked.has(p.id);
                        return (
                          <tr key={p.id}
                              className={isChecked ? "row-selected" : ""}
                              style={{ cursor: "pointer", background: p.id === selectedId ? "var(--accent-soft)" : undefined }}>
                            <td className="check" onClick={(e) => { e.stopPropagation(); toggleRow(p.id); }}>
                              <input type="checkbox" checked={isChecked} onChange={() => toggleRow(p.id)} onClick={(e) => e.stopPropagation()} />
                            </td>
                            <td onClick={() => setSelectedId(p.id)}><span className={`ftype ${ftypeClass(p.assessment_type)}`}>{ftypeLabel(p.assessment_type)}</span></td>
                            <td onClick={() => setSelectedId(p.id)}>{displayName(p)}</td>
                            {!split && (
                              <td onClick={() => setSelectedId(p.id)} className="mono mute" title={p.assessment_type ?? ""}>
                                {(p.assessment_type ?? "—").replace(/_/g, " ")}
                              </td>
                            )}
                            <td onClick={() => setSelectedId(p.id)} className="mono">{p.host}</td>
                            {!split && (() => {
                              // Committed → show the clean name on the share, hover for the source name.
                              // Uncommitted → still the source filename, since that's all we have.
                              const committedName = p.dest_path?.split("\\").pop() || null;
                              const shown = committedName || p.filename;
                              const tip = committedName ? `Source: ${p.filename}` : p.filename;
                              return <td onClick={() => setSelectedId(p.id)} className="mono" title={tip}>{shown}</td>;
                            })()}
                            {!split && <td onClick={() => setSelectedId(p.id)} className="mono mute" style={{ textAlign: "right" }}>{fmtBytes(p.size)}</td>}
                            {!split && <td onClick={() => setSelectedId(p.id)} className="mono mute">{fmtDate(p.mtime)}</td>}
                            <td onClick={() => setSelectedId(p.id)} className="mono mute">{fmtDate(p.indexed_at)}</td>
                            <td onClick={() => setSelectedId(p.id)}>
                              {p.archived_at
                                ? <span className="pill" title={`Archived ${p.archived_at}`} style={{ background: "var(--surface-2)", color: "var(--mute)", border: "1px solid var(--border-strong)" }}>Archived</span>
                                : p.committed_at
                                  ? <span className="pill pill-ok" title={`Committed ${p.committed_at}`}>Committed</span>
                                  : <span className="pill pill-warn">Pending</span>}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
                <div className="pager">
                  <span className="mono mute">
                    {rows.length === 0
                      ? "0 of 0"
                      : `${startIdx + 1}–${Math.min(startIdx + pageSize, rows.length)} of ${rows.length}`}
                  </span>
                  <div className="spacer" />
                  <button className="btn" onClick={() => setPage(1)} disabled={clampedPage === 1}>«</button>
                  <button className="btn" onClick={() => setPage((p) => Math.max(1, p - 1))} disabled={clampedPage === 1}>‹ Prev</button>
                  <span className="mono" style={{ fontSize: 12, padding: "0 8px" }}>Page {clampedPage} of {totalPages}</span>
                  <button className="btn" onClick={() => setPage((p) => Math.min(totalPages, p + 1))} disabled={clampedPage === totalPages}>Next ›</button>
                  <button className="btn" onClick={() => setPage(totalPages)} disabled={clampedPage === totalPages}>»</button>
                </div>
                {checked.size > 0 && (() => {
                  const selectedArchivedCount = selectedRows.filter((p) => p.archived_at).length;
                  const selectedActiveCount = checked.size - selectedArchivedCount;
                  return (
                    <div className="bulk-bar">
                      <span><strong>{checked.size}</strong> selected</span>
                      <div className="mute" style={{ fontSize: 11, color: "rgba(255,255,255,0.6)" }}>
                        {isArchivedView
                          ? `${selectedArchivedCount} archived`
                          : isMixedView
                            ? `${selectedActiveCount} active · ${selectedArchivedCount} archived`
                            : selectedPending > 0 ? `${selectedPending} pending commit` : "all committed"}
                      </div>
                      <div className="spacer" />
                      {!isArchivedView && (
                        <button className="btn" onClick={doCommit} disabled={!!running || selectedPending === 0}>Commit selected</button>
                      )}
                      {(!isArchivedView && selectedActiveCount > 0) && (
                        <button className="btn" onClick={doArchive} disabled={!!running}>Archive selected</button>
                      )}
                      {(isArchivedView || selectedArchivedCount > 0) && (
                        <button className="btn" onClick={doRestore} disabled={!!running}>Restore selected</button>
                      )}
                      <button className="btn" onClick={doExport}>Export CSV</button>
                      <button className="btn btn-danger" onClick={() => setConfirmDelete(true)} disabled={!!running}>Delete…</button>
                      <button className="btn btn-clear" onClick={clearSelection}>✕ Clear</button>
                    </div>
                  );
                })()}
              </>
            )}
          </div>
        </div>

        {split && (
          <div style={{
            flex: "1 1 auto",
            minWidth: 0,
            borderLeft: "1px solid var(--border-strong)",
            padding: "16px 20px 16px 0",
            display: "flex",
          }}>
            <div className="card" style={{ overflow: "hidden", flex: 1, display: "flex", flexDirection: "column", minHeight: 0 }}>
              {selectedPdf
                ? <PdfPanel pdf={selectedPdf} onClose={() => setSelectedId(null)} />
                : <PdfPanelEmpty />}
            </div>
          </div>
        )}
      </div>

      {/* Delete confirm modal */}
      <Dialog.Root open={confirmDelete} onOpenChange={setConfirmDelete}>
        <Dialog.Portal>
          <Dialog.Overlay className="drawer-overlay" />
          <Dialog.Content className="modal" style={{ width: "min(520px, 92vw)" }}>
            <div className="modal-head">
              <Dialog.Title style={{ fontSize: 15, fontWeight: 600 }}>Delete {checked.size} row(s)?</Dialog.Title>
              <div className="mute" style={{ fontSize: 12.5, marginTop: 4 }}>
                Removes the index entries for the selected files. Files on disk are only touched if you tick the option below.
              </div>
            </div>
            <div className="modal-body" style={{ padding: "16px 22px" }}>
              <label style={{ display: "flex", alignItems: "flex-start", gap: 10, cursor: "pointer" }}>
                <input type="checkbox" checked={deleteFiles} onChange={(e) => setDeleteFiles(e.target.checked)}
                       style={{ marginTop: 3, width: 14, height: 14, cursor: "pointer" }} />
                <span>
                  <div style={{ fontSize: 13, fontWeight: 500 }}>Also delete the file{checked.size > 1 ? "s" : ""} on disk</div>
                  <div className="mute" style={{ fontSize: 11.5, marginTop: 2 }}>
                    Removes committed files from the network share, pending files from the source PC.
                    Cannot be undone. Missing files are logged but not treated as errors.
                  </div>
                </span>
              </label>
            </div>
            <div className="modal-foot" style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
              <Dialog.Close asChild><button className="btn">Cancel</button></Dialog.Close>
              <button className="btn" style={{ background: "var(--err)", color: "white", borderColor: "var(--err)" }} onClick={doDelete}>
                {deleteFiles ? "Delete row & file" : "Delete row"}{checked.size > 1 ? "s" : ""}
              </button>
            </div>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
    </div>
  );
}
