"use client";

// Dashboard widget registry. Each widget is a self-contained component that
// reads from useApp() or fetches its own data. Registry keys are stored in
// users.dashboard_widgets (or derived from the profile's layout_key when
// the user hasn't customized).
//
// To add a new widget: implement the component, register it in WIDGETS with
// a kind ("stat" or "card") and a label, then optionally add its key to
// PROFILE_DEFAULTS if it should appear on a stock profile.

import Link from "next/link";
import { useEffect, useState } from "react";
import { useApp } from "../app-provider";
import { useAuth } from "../auth-provider";
import { PdfDrawer } from "../pdf-drawer";
import { apiFetch } from "../../_lib/auth";
import { displayName, fmtRelative, formatAssessmentType, ftypeClass, ftypeLabel } from "../util";
import { IconPlay, IconRefresh, IconUpload } from "../icons";

const TOTAL_PCS = 24;

// ---------- stat tiles -----------------------------------------------

function StatTotalFiles() {
  const { pdfs } = useApp();
  const hostsSeen = new Set(pdfs.map((p) => p.host)).size;
  return (
    <div className="stat">
      <div className="stat-label">Total Files</div>
      <div className="stat-value">{pdfs.length}</div>
      <div className="stat-meta">Across {hostsSeen}/{TOTAL_PCS} PCs seen</div>
    </div>
  );
}

function StatCommitted() {
  const { pdfs } = useApp();
  const committed = pdfs.filter((p) => p.committed_at).length;
  return (
    <div className="stat">
      <div className="stat-label">Committed</div>
      <div className="stat-value" style={{ color: "var(--ok)" }}>{committed}</div>
      <div className="stat-meta">Copied to network share</div>
    </div>
  );
}

function StatCommittedThisWeek() {
  const { pdfs } = useApp();
  const weekMs = 7 * 24 * 3600 * 1000;
  const now = Date.now();
  const n = pdfs.filter((p) => p.committed_at && now - new Date(p.committed_at).getTime() < weekMs).length;
  return (
    <div className="stat">
      <div className="stat-label">Committed This Week</div>
      <div className="stat-value" style={{ color: "var(--ok)" }}>{n}</div>
      <div className="stat-meta">Copied in last 7 days</div>
    </div>
  );
}

function StatPending() {
  const { pdfs } = useApp();
  const pending = pdfs.filter((p) => !p.committed_at).length;
  return (
    <div className="stat">
      <div className="stat-label">Pending</div>
      <div className="stat-value" style={{ color: pending ? "var(--warn)" : "var(--muted)" }}>{pending}</div>
      <div className="stat-meta">Indexed, awaiting commit</div>
    </div>
  );
}

function StatLabPCs() {
  return (
    <div className="stat">
      <div className="stat-label">Lab PCs</div>
      <div className="stat-value">{TOTAL_PCS}</div>
      <div className="stat-meta">PC1 – PC{TOTAL_PCS}</div>
    </div>
  );
}

function StatAssessmentTypes() {
  const { pdfs } = useApp();
  const distinctTypes = new Set(
    pdfs.map((p) => p.assessment_type).filter((t): t is string => Boolean(t)),
  ).size;
  return (
    <Link
      href="/assessments"
      className="stat"
      style={{ textDecoration: "none", color: "inherit", cursor: "pointer" }}
      title="View assessments grouped by type"
    >
      <div className="stat-label">Assessment Types</div>
      <div className="stat-value" style={{ color: "var(--accent)" }}>{distinctTypes}</div>
      <div className="stat-meta">Click to browse by type →</div>
    </Link>
  );
}

// ---------- PC-fleet stat tiles --------------------------------------

type Pc = {
  pc_name: string;
  host: string;
  last_attempt: string | null;
  last_seen: string | null;
  reachable: boolean | null;
  files_indexed: number;
};

function usePCs() {
  const [pcs, setPcs] = useState<Pc[]>([]);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    let alive = true;
    apiFetch("/api/pcs")
      .then((r) => r.json())
      .then((data) => { if (alive) setPcs(data); })
      .catch(() => { /* leave empty */ })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, []);
  return { pcs, loading };
}

function StatPCsReachable() {
  const { pcs } = usePCs();
  const n = pcs.filter((p) => p.reachable).length;
  return (
    <div className="stat">
      <div className="stat-label">Reachable</div>
      <div className="stat-value" style={{ color: "var(--ok)" }}>{n}</div>
      <div className="stat-meta">Answered last scan</div>
    </div>
  );
}

function StatPCsUnreachable() {
  const { pcs } = usePCs();
  const n = pcs.filter((p) => p.reachable === false).length;
  return (
    <div className="stat">
      <div className="stat-label">Unreachable</div>
      <div className="stat-value" style={{ color: n ? "var(--err)" : "var(--muted)" }}>{n}</div>
      <div className="stat-meta">Offline / auth failed</div>
    </div>
  );
}

function StatPCsNeverScanned() {
  const { pcs } = usePCs();
  const n = pcs.filter((p) => p.reachable === null).length;
  return (
    <div className="stat">
      <div className="stat-label">Never Scanned</div>
      <div className="stat-value" style={{ color: n ? "var(--warn)" : "var(--muted)" }}>{n}</div>
      <div className="stat-meta">Awaiting first scan</div>
    </div>
  );
}

// ---------- cards ----------------------------------------------------

function CardQuickActions() {
  const { run, running, refresh } = useApp();
  const { me } = useAuth();
  const canTrigger = (me?.permissions ?? []).includes("run:trigger");
  const { pdfs } = useApp();
  const pending = pdfs.filter((p) => !p.committed_at).length;

  return (
    <div className="card">
      <div className="card-head">
        <div className="card-title">QUICK ACTIONS</div>
      </div>
      <div className="card-body">
        <div className="qa-list">
          {canTrigger ? (
            <>
              <div className="qa" onClick={() => !running && run("scan")} style={running ? { opacity: 0.5 } : undefined}>
                <div className="qa-icon"><IconPlay /></div>
                <div className="qa-text">
                  <div className="qa-title">Run scan</div>
                  <div className="qa-desc">Discover new PDFs on all 24 PCs</div>
                </div>
              </div>
              <div className="qa" onClick={() => !running && run("commit")} style={running ? { opacity: 0.5 } : undefined}>
                <div className="qa-icon"><IconUpload /></div>
                <div className="qa-text">
                  <div className="qa-title">Commit pending</div>
                  <div className="qa-desc">{pending > 0 ? `Copy ${pending} to network share` : "Nothing to commit"}</div>
                </div>
              </div>
              <div className="qa" onClick={() => refresh()}>
                <div className="qa-icon"><IconRefresh /></div>
                <div className="qa-text">
                  <div className="qa-title">Refresh index</div>
                  <div className="qa-desc">Reload the file list from the database</div>
                </div>
              </div>
            </>
          ) : (
            <div className="mute" style={{ fontSize: 12.5, padding: "8px 4px" }}>
              Read-only account — scans and commits are run by admins and operators.
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function CardRecentFiles({ rows = 6, showCommitted = false }: { rows?: number; showCommitted?: boolean }) {
  const { pdfs, run, running } = useApp();
  const { me } = useAuth();
  const canTrigger = (me?.permissions ?? []).includes("run:trigger");
  const recent = pdfs.slice(0, rows);
  const [selectedId, setSelectedId] = useState<number | null>(null);

  return (
    <div className="card">
      <div className="card-head">
        <div className="card-title">RECENT FILES</div>
        <Link href="/files" className="card-more">View all →</Link>
      </div>
      <div>
        {recent.length === 0 ? (
          <div className="empty" style={{ padding: "40px 20px" }}>
            <h3>No files indexed yet</h3>
            <p>
              {canTrigger
                ? "Run a scan to discover PDFs across the 24 lab PCs."
                : "Nothing yet — an admin or operator can run the first scan."}
            </p>
            {canTrigger && (
              <button className="btn btn-primary" onClick={() => run("scan")} disabled={!!running}>
                <IconPlay /> Run first scan
              </button>
            )}
          </div>
        ) : (
          <table className="tbl">
            <thead>
              <tr>
                <th style={{ width: 50 }}></th>
                <th>Client</th>
                <th>Assessment</th>
                <th style={{ width: 90 }}>Status</th>
                <th style={{ width: 100 }}>Indexed</th>
                {showCommitted && <th style={{ width: 110 }}>Committed</th>}
              </tr>
            </thead>
            <tbody>
              {recent.map((p) => (
                <tr key={p.id} onClick={() => setSelectedId(p.id)} style={{ cursor: "pointer" }}>
                  <td><span className={`ftype ${ftypeClass(p.assessment_type)}`}>{ftypeLabel(p.assessment_type)}</span></td>
                  <td>{displayName(p)}</td>
                  <td className="mono mute" title={p.assessment_type ?? ""}>{formatAssessmentType(p.assessment_type)}</td>
                  <td>
                    {p.committed_at
                      ? <span className="pill pill-ok">Committed</span>
                      : <span className="pill pill-warn">Pending</span>}
                  </td>
                  <td className="mono mute">{fmtRelative(p.indexed_at)}</td>
                  {showCommitted && <td className="mono mute">{p.committed_at ? fmtRelative(p.committed_at) : "—"}</td>}
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
      <PdfDrawer
        pdf={selectedId ? (pdfs.find((p) => p.id === selectedId) ?? null) : null}
        open={selectedId !== null}
        onClose={() => setSelectedId(null)}
      />
    </div>
  );
}

function CardRecentFilesShort() { return <CardRecentFiles rows={6} showCommitted={false} />; }
function CardRecentFilesLarge() { return <CardRecentFiles rows={12} showCommitted />; }

function healthRank(pc: Pc): number {
  if (pc.reachable === false) return 0;
  if (pc.reachable === null) return 1;
  const seen = pc.last_seen ? new Date(pc.last_seen).getTime() : 0;
  if (Date.now() - seen > 24 * 3600 * 1000) return 2;
  return 3;
}

function CardPCHealth() {
  const { pcs, loading } = usePCs();
  const sorted = [...pcs].sort((a, b) => healthRank(a) - healthRank(b));
  return (
    <div className="card">
      <div className="card-head">
        <div className="card-title">PC HEALTH</div>
        <Link href="/pcs" className="card-more">View all →</Link>
      </div>
      <div className="card-body">
        {loading ? (
          <div className="mute" style={{ padding: 16, fontSize: 12 }}>Loading…</div>
        ) : (
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(180px, 1fr))", gap: 8 }}>
            {sorted.slice(0, 12).map((p) => {
              const rank = healthRank(p);
              const dot = rank === 0 ? "var(--err)" : rank === 1 || rank === 2 ? "var(--warn)" : "var(--ok)";
              const label = rank === 0 ? "Unreachable" : rank === 1 ? "Never seen" : rank === 2 ? "Stale" : "OK";
              return (
                <div key={p.pc_name} style={{
                  padding: "10px 12px", background: "var(--surface)",
                  border: "1px solid var(--border)", borderRadius: 3,
                }}>
                  <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                    <span style={{ fontSize: 12, fontWeight: 600 }}>{p.pc_name}</span>
                    <span className="status-dot" style={{
                      background: dot,
                      boxShadow: `0 0 0 3px color-mix(in oklab, ${dot} 15%, transparent)`,
                    }} />
                  </div>
                  <div className="mono mute" style={{ fontSize: 11, marginTop: 2 }}>{label}</div>
                  <div className="mono mute" style={{ fontSize: 10, marginTop: 4 }}>
                    {p.last_attempt ? fmtRelative(p.last_attempt) : "—"}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}

type Run = {
  id: number;
  mode: string;
  started_at: string;
  ended_at: string | null;
  counts: Record<string, number> | null;
  error: string | null;
};

function CardRecentScanRuns() {
  const [runs, setRuns] = useState<Run[]>([]);
  useEffect(() => {
    let alive = true;
    apiFetch("/api/runs?limit=5")
      .then((r) => r.json())
      .then((data) => { if (alive) setRuns(data); })
      .catch(() => { /* leave empty */ });
    return () => { alive = false; };
  }, []);
  return (
    <div className="card">
      <div className="card-head">
        <div className="card-title">RECENT SCAN RUNS</div>
        <Link href="/logs" className="card-more">View all →</Link>
      </div>
      <div>
        {runs.length === 0 ? (
          <div className="empty" style={{ padding: "40px 20px" }}>
            <h3>No scans yet</h3>
            <p>No scans or commits have been run.</p>
          </div>
        ) : (
          <table className="tbl">
            <thead>
              <tr><th>Mode</th><th>Started</th><th>Result</th></tr>
            </thead>
            <tbody>
              {runs.map((r) => {
                const summary = r.error
                  ? r.error.slice(0, 60)
                  : r.counts
                    ? `${r.counts.new ?? 0} new · ${r.counts.updated ?? 0} updated · ${r.counts.unchanged ?? 0} same`
                    : "—";
                return (
                  <tr key={r.id}>
                    <td className="mono">{r.mode}</td>
                    <td className="mono mute">{fmtRelative(r.started_at)}</td>
                    <td className="mono mute" style={{ fontSize: 11 }} title={summary}>
                      {r.error ? <span style={{ color: "var(--err)" }}>{summary}</span> : summary}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

// ---------- registry -------------------------------------------------

export type WidgetKind = "stat" | "card";

export type WidgetDef = {
  key: string;
  kind: WidgetKind;
  label: string;
  description: string;
  component: React.ComponentType;
};

export const WIDGETS: Record<string, WidgetDef> = {
  stat_total_files:         { key: "stat_total_files",         kind: "stat", label: "Total Files",          description: "Count of active files with hosts-seen meta.", component: StatTotalFiles },
  stat_committed:           { key: "stat_committed",           kind: "stat", label: "Committed",            description: "Files copied to the network share.",          component: StatCommitted },
  stat_committed_this_week: { key: "stat_committed_this_week", kind: "stat", label: "Committed This Week",  description: "Committed in the last 7 days.",               component: StatCommittedThisWeek },
  stat_pending:             { key: "stat_pending",             kind: "stat", label: "Pending",              description: "Indexed, awaiting commit.",                   component: StatPending },
  stat_lab_pcs:             { key: "stat_lab_pcs",             kind: "stat", label: "Lab PCs",              description: "Static tile: PC1 – PC24.",                    component: StatLabPCs },
  stat_assessment_types:    { key: "stat_assessment_types",    kind: "stat", label: "Assessment Types",     description: "Distinct types seen. Click to browse by type.", component: StatAssessmentTypes },
  stat_pcs_reachable:       { key: "stat_pcs_reachable",       kind: "stat", label: "PCs Reachable",        description: "PCs that answered the last scan.",            component: StatPCsReachable },
  stat_pcs_unreachable:     { key: "stat_pcs_unreachable",     kind: "stat", label: "PCs Unreachable",      description: "PCs offline or auth-failed on the last scan.", component: StatPCsUnreachable },
  stat_pcs_never_scanned:   { key: "stat_pcs_never_scanned",   kind: "stat", label: "PCs Never Scanned",    description: "PCs with no scan attempt yet.",               component: StatPCsNeverScanned },
  card_quick_actions:       { key: "card_quick_actions",       kind: "card", label: "Quick Actions",        description: "Run scan / Commit / Refresh buttons.",        component: CardQuickActions },
  card_recent_files:        { key: "card_recent_files",        kind: "card", label: "Recent Files",         description: "Six newest files with status.",               component: CardRecentFilesShort },
  card_recent_files_large:  { key: "card_recent_files_large",  kind: "card", label: "Recent Files (large)", description: "Twelve newest files with a Committed column.", component: CardRecentFilesLarge },
  card_pc_health:           { key: "card_pc_health",           kind: "card", label: "PC Health",            description: "Grid of the 12 unhealthiest / stalest PCs.",  component: CardPCHealth },
  card_recent_scan_runs:    { key: "card_recent_scan_runs",    kind: "card", label: "Recent Scan Runs",     description: "Last 5 scans/commits with result summary.",   component: CardRecentScanRuns },
};

// ---------- profile defaults -----------------------------------------

export const PROFILE_DEFAULTS: Record<string, string[]> = {
  ops_default: [
    "stat_total_files", "stat_committed", "stat_pending", "stat_lab_pcs",
    "card_quick_actions", "card_recent_files",
  ],
  fleet_health: [
    "stat_pcs_reachable", "stat_pcs_unreachable", "stat_pcs_never_scanned", "stat_lab_pcs",
    "card_pc_health", "card_recent_scan_runs",
  ],
  records: [
    "stat_total_files", "stat_committed_this_week", "stat_pending", "stat_assessment_types",
    "card_recent_files_large",
  ],
};

export function widgetsForProfile(layoutKey: string | undefined | null): string[] {
  return PROFILE_DEFAULTS[layoutKey ?? "ops_default"] ?? PROFILE_DEFAULTS.ops_default;
}
