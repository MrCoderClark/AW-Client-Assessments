"use client";

import { useEffect, useState } from "react";
import { fmtRelative } from "../_components/util";
import { PcExplorerDialog } from "../_components/pc-explorer-dialog";
import { RequirePerm } from "../_components/require-perm";
import { apiFetch } from "../_lib/auth";

// ponytail: same-origin via next.config rewrite; use apiFetch for auth headers.
const API = "";

type Pc = {
  pc_name: string;
  host: string;
  last_attempt: string | null;
  last_seen: string | null;
  reachable: boolean | null;
  error: string | null;
  counts: { seen: number; new: number; updated: number; unchanged: number; noassess: number; locked: number } | null;
  files_indexed: number;
};

function stateOf(pc: Pc): { pill: "ok" | "warn" | "err" | "faint"; label: string } {
  if (pc.reachable === null) return { pill: "faint", label: "Never scanned" };
  if (pc.reachable) return { pill: "ok", label: "Reachable" };
  return { pill: "err", label: "Unreachable" };
}

export default function PcsPage() {
  return <RequirePerm perms={["pc:read"]} blockedRoles={["viewer"]}><PcsPageInner /></RequirePerm>;
}

function PcsPageInner() {
  const [pcs, setPcs] = useState<Pc[]>([]);
  const [loading, setLoading] = useState(true);
  const [explorerFor, setExplorerFor] = useState<Pc | null>(null);

  const refresh = async () => {
    setLoading(true);
    try {
      const r = await apiFetch(`${API}/api/pcs`);
      setPcs(await r.json());
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { refresh(); }, []);

  // Open a specific PC's explorer via ?open=<pc_name> (from command palette).
  // ponytail: raw location + wait for pcs to load so we can pass host through.
  useEffect(() => {
    if (!pcs.length) return;
    const name = new URLSearchParams(window.location.search).get("open");
    if (!name) return;
    const pc = pcs.find((p) => p.pc_name === name);
    if (pc) setExplorerFor(pc);
  }, [pcs]);

  const reachable = pcs.filter((p) => p.reachable).length;
  const unreachable = pcs.filter((p) => p.reachable === false).length;
  const never = pcs.filter((p) => p.reachable === null).length;

  return (
    <div className="section-pad">
      <div className="stat-grid" style={{ gridTemplateColumns: "repeat(3, 1fr)" }}>
        <div className="stat">
          <div className="stat-label">Reachable</div>
          <div className="stat-value" style={{ color: "var(--ok)" }}>{reachable}</div>
          <div className="stat-meta">Successful in last scan</div>
        </div>
        <div className="stat">
          <div className="stat-label">Unreachable</div>
          <div className="stat-value" style={{ color: unreachable ? "var(--err)" : "var(--muted)" }}>{unreachable}</div>
          <div className="stat-meta">Offline / auth failed / firewall</div>
        </div>
        <div className="stat">
          <div className="stat-label">Never Scanned</div>
          <div className="stat-value" style={{ color: never ? "var(--warn)" : "var(--muted)" }}>{never}</div>
          <div className="stat-meta">Awaiting first scan</div>
        </div>
      </div>

      <div className="card">
        <div className="card-head">
          <div className="card-title">LAB PCs · {pcs.length} total</div>
          <div className="mono mute" style={{ fontSize: 11 }}>Updated from last scan</div>
        </div>
        {loading ? (
          <div className="empty"><p>Loading…</p></div>
        ) : (
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(220px, 1fr))", gap: 1, background: "var(--border)" }}>
            {pcs.map((p) => {
              const s = stateOf(p);
              return (
                <div key={p.pc_name}
                     onClick={() => setExplorerFor(p)}
                     style={{ background: "var(--surface)", padding: "14px 16px", display: "flex", flexDirection: "column", gap: 6, cursor: "pointer", transition: "background 80ms" }}
                     onMouseEnter={(e) => e.currentTarget.style.background = "var(--bg)"}
                     onMouseLeave={(e) => e.currentTarget.style.background = "var(--surface)"}>
                  <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                    <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                      <span className={`status-dot${s.pill === "ok" ? "" : " off"}`} style={{
                        background: s.pill === "ok" ? "var(--ok)" : s.pill === "err" ? "var(--err)" : "var(--faint)",
                        boxShadow: `0 0 0 3px color-mix(in oklab, ${s.pill === "ok" ? "var(--ok)" : s.pill === "err" ? "var(--err)" : "var(--faint)"} 15%, transparent)`,
                      }} />
                      <span style={{ fontWeight: 600, fontSize: 13 }}>{p.pc_name}</span>
                    </div>
                    <span className={`pill pill-${s.pill === "faint" ? "warn" : s.pill}`} style={s.pill === "faint" ? { background: "transparent", color: "var(--muted)" } : undefined}>
                      {s.label}
                    </span>
                  </div>
                  <div className="mono mute" style={{ fontSize: 11 }}>{p.host}</div>
                  <div style={{ display: "flex", justifyContent: "space-between", fontSize: 11, color: "var(--muted)", borderTop: "1px solid var(--border)", paddingTop: 6, marginTop: 2 }}>
                    <span>{p.files_indexed} files indexed</span>
                    <span>{p.last_attempt ? fmtRelative(p.last_attempt) : "—"}</span>
                  </div>
                  {p.error && <div className="mono" style={{ fontSize: 11, color: "var(--err)", marginTop: 2 }} title={p.error}>{p.error.slice(0, 60)}{p.error.length > 60 ? "…" : ""}</div>}
                </div>
              );
            })}
          </div>
        )}
      </div>

      <PcExplorerDialog
        open={explorerFor !== null}
        onClose={() => setExplorerFor(null)}
        pcName={explorerFor?.pc_name ?? null}
        host={explorerFor?.host ?? null}
      />
    </div>
  );
}
