"use client";

import { useEffect, useMemo, useState } from "react";
import { fmtRelative } from "../_components/util";
import { IconRefresh, IconSearch } from "../_components/icons";
import { PcDetailDialog } from "../_components/pc-detail-dialog";
import { RequirePerm } from "../_components/require-perm";
import { apiFetch } from "../_lib/auth";

// ponytail: same-origin via next.config rewrite; use apiFetch for auth headers.
const API = "";
const POLL_MS = 15_000;

type Row = {
  pc_name: string;
  host: string;
  desktop: number;
  documents: number;
  downloads: number;
  other: number;
  total: number;
  committed: number;
  pending: number;
  last_attempt: string | null;
  last_seen: string | null;
  reachable: boolean | null;
  error: string | null;
};

type SortKey = "pc_name" | "total" | "committed" | "pending" | "last_attempt";

function pcNumber(name: string): number {
  const m = name.match(/\d+/);
  return m ? Number(m[0]) : 999;
}

function statusPill(r: Row) {
  if (r.reachable === null) return { cls: "pill-warn", label: "Never scanned" };
  if (r.reachable) return { cls: "pill-ok", label: "Reachable" };
  return { cls: "pill-err", label: "Unreachable" };
}

function Cell({ value }: { value: number }) {
  const zero = value === 0;
  return (
    <span className="mono" style={{ color: zero ? "var(--faint)" : "var(--ink)", fontWeight: zero ? 400 : 600, fontSize: 13 }}>
      {value}
    </span>
  );
}

export default function LogsPage() {
  return <RequirePerm perms={["log:read"]} blockedRoles={["viewer"]}><LogsPageInner /></RequirePerm>;
}

function LogsPageInner() {
  const [rows, setRows] = useState<Row[]>([]);
  const [loading, setLoading] = useState(true);
  const [q, setQ] = useState("");
  const [sortKey, setSortKey] = useState<SortKey>("pc_name");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("asc");
  const [showEmpty, setShowEmpty] = useState(true);
  const [selectedPc, setSelectedPc] = useState<Row | null>(null);

  const load = async () => {
    try {
      const r = await apiFetch(`${API}/api/logs`);
      setRows(await r.json());
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => { load(); }, []);
  useEffect(() => {
    const t = setInterval(load, POLL_MS);
    return () => clearInterval(t);
  }, []);

  const filtered = useMemo(() => {
    const term = q.trim().toLowerCase();
    let list = rows;
    if (!showEmpty) list = list.filter((r) => r.total > 0);
    if (term) list = list.filter((r) =>
      r.pc_name.toLowerCase().includes(term) || r.host.includes(term)
    );
    const dir = sortDir === "asc" ? 1 : -1;
    return [...list].sort((a, b) => {
      let av: string | number = 0, bv: string | number = 0;
      switch (sortKey) {
        case "pc_name":      av = pcNumber(a.pc_name); bv = pcNumber(b.pc_name); break;
        case "total":        av = a.total; bv = b.total; break;
        case "committed":    av = a.committed; bv = b.committed; break;
        case "pending":      av = a.pending; bv = b.pending; break;
        case "last_attempt": av = a.last_attempt ?? ""; bv = b.last_attempt ?? ""; break;
      }
      return av < bv ? -1 * dir : av > bv ? 1 * dir : 0;
    });
  }, [rows, q, sortKey, sortDir, showEmpty]);

  const totals = rows.reduce(
    (acc, r) => {
      acc.desktop += r.desktop; acc.documents += r.documents; acc.downloads += r.downloads;
      acc.total += r.total; acc.committed += r.committed; acc.pending += r.pending;
      if (r.reachable) acc.reachable++;
      return acc;
    },
    { desktop: 0, documents: 0, downloads: 0, total: 0, committed: 0, pending: 0, reachable: 0 },
  );

  const toggleSort = (k: SortKey) => {
    if (k === sortKey) setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    else { setSortKey(k); setSortDir(k === "pc_name" ? "asc" : "desc"); }
  };
  const arrow = (k: SortKey) => sortKey === k ? <span className="arrow">{sortDir === "asc" ? "↑" : "↓"}</span> : null;
  const thCls = (k: SortKey) => sortKey === k ? "sort-active" : "";

  return (
    <>
      {/* Header stats */}
      <div className="section-pad" style={{ paddingBottom: 0 }}>
        <div className="stat-grid" style={{ gridTemplateColumns: "repeat(5, 1fr)" }}>
          <div className="stat">
            <div className="stat-label">Reachable PCs</div>
            <div className="stat-value" style={{ color: "var(--ok)" }}>{totals.reachable}<span style={{ color: "var(--muted)", fontSize: 14, fontWeight: 400 }}> / {rows.length}</span></div>
          </div>
          <div className="stat">
            <div className="stat-label">Desktop</div>
            <div className="stat-value">{totals.desktop}</div>
          </div>
          <div className="stat">
            <div className="stat-label">Documents</div>
            <div className="stat-value">{totals.documents}</div>
          </div>
          <div className="stat">
            <div className="stat-label">Downloads</div>
            <div className="stat-value">{totals.downloads}</div>
          </div>
          <div className="stat">
            <div className="stat-label">Pending Commit</div>
            <div className="stat-value" style={{ color: totals.pending ? "var(--warn)" : "var(--muted)" }}>{totals.pending}</div>
            <div className="stat-meta">{totals.committed} committed of {totals.total} total</div>
          </div>
        </div>
      </div>

      {/* Toolbar */}
      <div className="toolbar">
        <div className="search">
          <span className="search-icon"><IconSearch /></span>
          <input placeholder="Search PC or IP…" value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
        <div className="chips">
          <button className={`chip${showEmpty ? " active" : ""}`}   onClick={() => setShowEmpty(true)}>All PCs</button>
          <button className={`chip${!showEmpty ? " active" : ""}`}  onClick={() => setShowEmpty(false)}>With files only</button>
        </div>
        <div className="spacer" />
        <button className="btn" onClick={load}><IconRefresh /> Refresh</button>
      </div>

      <div className="section-pad">
        <div className="card" style={{ overflow: "hidden" }}>
          {loading ? (
            <div className="empty"><p>Loading…</p></div>
          ) : filtered.length === 0 ? (
            <div className="empty">
              <h3>No PCs to show</h3>
              <p>{rows.length === 0 ? "No data yet — run a scan." : "Try changing the filter."}</p>
            </div>
          ) : (
            <div style={{ overflow: "auto", maxHeight: "calc(100vh - 60px - 60px - 174px - 32px)" }}>
              <table className="tbl">
                <thead>
                  <tr>
                    <th className={thCls("pc_name")} onClick={() => toggleSort("pc_name")} style={{ width: 90 }}>PC {arrow("pc_name")}</th>
                    <th style={{ width: 140 }}>Host</th>
                    <th style={{ textAlign: "center", width: 90 }}>Desktop</th>
                    <th style={{ textAlign: "center", width: 100 }}>Documents</th>
                    <th style={{ textAlign: "center", width: 100 }}>Downloads</th>
                    <th className={thCls("total")} onClick={() => toggleSort("total")} style={{ textAlign: "center", width: 80 }}>Total {arrow("total")}</th>
                    <th className={thCls("committed")} onClick={() => toggleSort("committed")} style={{ textAlign: "center", width: 100 }}>Committed {arrow("committed")}</th>
                    <th className={thCls("pending")} onClick={() => toggleSort("pending")} style={{ textAlign: "center", width: 90 }}>Pending {arrow("pending")}</th>
                    <th style={{ width: 130 }}>Status</th>
                    <th className={thCls("last_attempt")} onClick={() => toggleSort("last_attempt")} style={{ width: 130 }}>Last Scan {arrow("last_attempt")}</th>
                  </tr>
                </thead>
                <tbody>
                  {filtered.map((r) => {
                    const s = statusPill(r);
                    return (
                      <tr key={r.pc_name} onClick={() => setSelectedPc(r)} style={{ cursor: "pointer" }}>
                        <td style={{ fontWeight: 600 }}>{r.pc_name}</td>
                        <td className="mono mute">{r.host}</td>
                        <td style={{ textAlign: "center" }}><Cell value={r.desktop} /></td>
                        <td style={{ textAlign: "center" }}><Cell value={r.documents} /></td>
                        <td style={{ textAlign: "center" }}><Cell value={r.downloads} /></td>
                        <td style={{ textAlign: "center" }}>
                          <span className="mono" style={{ fontSize: 13, fontWeight: 600, color: r.total ? "var(--ink)" : "var(--faint)" }}>{r.total}</span>
                        </td>
                        <td style={{ textAlign: "center" }}>
                          <span className="mono" style={{ fontSize: 13, color: r.committed ? "var(--ok)" : "var(--faint)", fontWeight: r.committed ? 600 : 400 }}>{r.committed}</span>
                        </td>
                        <td style={{ textAlign: "center" }}>
                          {r.pending > 0
                            ? <span className="pill pill-warn">{r.pending}</span>
                            : <span className="mono" style={{ color: "var(--faint)" }}>0</span>}
                        </td>
                        <td>
                          <span className={`pill ${s.cls}`}>{s.label}</span>
                        </td>
                        <td className="mono mute">{r.last_attempt ? fmtRelative(r.last_attempt) : "—"}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>

      <PcDetailDialog
        open={selectedPc !== null}
        onClose={() => setSelectedPc(null)}
        pcName={selectedPc?.pc_name ?? null}
        host={selectedPc?.host ?? null}
        lastAttempt={selectedPc?.last_attempt}
        reachable={selectedPc?.reachable}
        error={selectedPc?.error}
      />
    </>
  );
}
