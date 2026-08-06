"use client";

import { useEffect, useState } from "react";
import { fmtDate, fmtRelative } from "../_components/util";
import { useApp } from "../_components/app-provider";
import { IconPlay, IconRefresh, IconUpload } from "../_components/icons";
import { RequirePerm } from "../_components/require-perm";
import { apiFetch } from "../_lib/auth";

// ponytail: same-origin via next.config rewrite; use apiFetch for auth headers.
const API = "";
const DAYS = [
  { i: 0, label: "Mon" }, { i: 1, label: "Tue" }, { i: 2, label: "Wed" },
  { i: 3, label: "Thu" }, { i: 4, label: "Fri" }, { i: 5, label: "Sat" }, { i: 6, label: "Sun" },
];
const POLL_MS = 10_000;

type Schedule = {
  id: number;
  enabled: number;
  mode: "scan" | "scan+commit";
  time_of_day: string;
  weekdays: string;
  last_run_at: string | null;
  last_run_ok: number | null;
  next_run_at: string | null;
  email_on_commit: number;
};

type Run = {
  id: number;
  mode: string;
  started_at: string;
  ended_at: string | null;
  counts: Record<string, number> | null;
  error: string | null;
};

function untilPhrase(iso: string | null, now: number): string {
  if (!iso) return "—";
  const target = new Date(iso).getTime();
  let delta = Math.floor((target - now) / 1000);
  if (delta <= 0) return "any moment now…";
  const d = Math.floor(delta / 86400); delta %= 86400;
  const h = Math.floor(delta / 3600);  delta %= 3600;
  const m = Math.floor(delta / 60);
  const s = delta % 60;
  if (d > 0) return `in ${d}d ${h}h`;
  if (h > 0) return `in ${h}h ${m}m`;
  if (m > 0) return `in ${m}m ${s}s`;
  return `in ${s}s`;
}

export default function SettingsPage() {
  return (
    <RequirePerm perms={["schedule:write"]}>
      <SettingsPageInner />
    </RequirePerm>
  );
}

function SettingsPageInner() {
  const { run, running } = useApp();
  const [sched, setSched] = useState<Schedule | null>(null);
  const [runs, setRuns] = useState<Run[]>([]);
  const [saving, setSaving] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [now, setNow] = useState(Date.now());

  const load = async () => {
    try {
      const [sr, rr] = await Promise.all([
        apiFetch(`${API}/api/schedule`).then(r => r.json()),
        apiFetch(`${API}/api/runs?limit=5`).then(r => r.json()),
      ]);
      setSched(sr);
      setRuns(rr);
      setDirty(false);
    } catch (e) { console.error(e); }
  };

  useEffect(() => { load(); }, []);
  // Poll — matches the 30s backend loop; 10s here means UI is at most 10s behind.
  useEffect(() => {
    const t = setInterval(() => { if (!dirty && !saving) load(); }, POLL_MS);
    return () => clearInterval(t);
  }, [dirty, saving]);
  // Tick for the countdown.
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);

  const patch = (p: Partial<Schedule>) => {
    setSched((prev) => prev ? { ...prev, ...p } : prev);
    setDirty(true);
  };

  const save = async () => {
    if (!sched) return;
    setSaving(true);
    try {
      const r = await apiFetch(`${API}/api/schedule`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          enabled: !!sched.enabled,
          mode: sched.mode,
          time_of_day: sched.time_of_day,
          weekdays: sched.weekdays,
          email_on_commit: !!sched.email_on_commit,
        }),
      });
      setSched(await r.json());
      setDirty(false);
    } finally {
      setSaving(false);
    }
  };

  const toggleDay = (i: number) => {
    if (!sched) return;
    const set = new Set(sched.weekdays ? sched.weekdays.split(",").map(Number) : []);
    if (set.has(i)) set.delete(i); else set.add(i);
    const arr = [...set].sort((a, b) => a - b);
    patch({ weekdays: arr.join(",") });
  };

  if (!sched) {
    return <div className="section-pad"><div className="empty"><p>Loading…</p></div></div>;
  }

  const selectedDays = new Set(sched.weekdays ? sched.weekdays.split(",").map(Number) : []);
  const enabled = !!sched.enabled;
  const lastRunTs = sched.last_run_at ? new Date(sched.last_run_at.replace(" ", "T") + "Z").getTime() : 0;
  const justRan = lastRunTs > 0 && (now - lastRunTs) < 90_000;

  return (
    <div className="section-pad" style={{ maxWidth: 1400 }}>
      {/* Live status banner */}
      <div className="card" style={{ marginBottom: 16, borderLeft: `3px solid ${enabled ? "var(--accent)" : "var(--border-strong)"}` }}>
        <div className="card-body" style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "18px 20px" }}>
          <div>
            <div style={{ fontSize: 11, color: "var(--muted)", textTransform: "uppercase", letterSpacing: "0.08em", fontWeight: 500 }}>Next scheduled run</div>
            <div style={{ fontSize: 22, fontWeight: 600, letterSpacing: "-0.01em", marginTop: 4, color: enabled ? "var(--ink)" : "var(--muted)" }}>
              {enabled ? untilPhrase(sched.next_run_at, now) : "Disabled"}
            </div>
            <div className="mono mute" style={{ fontSize: 12, marginTop: 2 }}>
              {sched.next_run_at ? fmtDate(sched.next_run_at) : "—"}
            </div>
          </div>
          <div style={{ textAlign: "right" }}>
            <div style={{ fontSize: 11, color: "var(--muted)", textTransform: "uppercase", letterSpacing: "0.08em", fontWeight: 500 }}>Last run</div>
            <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 4, justifyContent: "flex-end" }}>
              {justRan && <span className="pill pill-ok"><span className="pulse" style={{ background: "var(--ok)" }} /> Just ran</span>}
              <div style={{ fontSize: 15, fontWeight: 500, color: sched.last_run_at ? (sched.last_run_ok ? "var(--ok)" : "var(--err)") : "var(--muted)" }}>
                {sched.last_run_at ? fmtRelative(sched.last_run_at) : "Never"}
              </div>
            </div>
            <div className="mono mute" style={{ fontSize: 12, marginTop: 2 }}>
              {sched.last_run_at ? fmtDate(sched.last_run_at) : "—"}
            </div>
          </div>
        </div>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16, alignItems: "start" }}>
      <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>

      <div className="card">
        <div className="card-head">
          <div className="card-title">SCHEDULED RUN</div>
          <button className="btn" onClick={load} style={{ height: 28, padding: "0 10px" }} title="Refresh"><IconRefresh /></button>
        </div>
        <div className="card-body" style={{ display: "flex", flexDirection: "column", gap: 20 }}>
          {/* Enable */}
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
            <div>
              <div style={{ fontSize: 13, fontWeight: 500 }}>Enable automatic runs</div>
              <div className="mute" style={{ fontSize: 12 }}>Replaces the old Windows Task Scheduler entry.</div>
            </div>
            <label style={{ position: "relative", display: "inline-block", width: 40, height: 22 }}>
              <input type="checkbox" checked={enabled} onChange={(e) => patch({ enabled: e.target.checked ? 1 : 0 })} style={{ opacity: 0, width: 0, height: 0 }} />
              <span style={{
                position: "absolute", cursor: "pointer", inset: 0,
                background: enabled ? "var(--accent)" : "var(--border-strong)",
                borderRadius: 22, transition: "background 120ms",
              }}>
                <span style={{
                  position: "absolute", top: 2, left: enabled ? 20 : 2,
                  width: 18, height: 18, background: "white", borderRadius: 18,
                  transition: "left 120ms",
                }} />
              </span>
            </label>
          </div>

          {/* Time */}
          <div>
            <label style={{ fontSize: 12, fontWeight: 500, color: "var(--muted)", textTransform: "uppercase", letterSpacing: "0.06em", display: "block", marginBottom: 6 }}>
              Time of day
            </label>
            <input
              type="time"
              value={sched.time_of_day}
              onChange={(e) => patch({ time_of_day: e.target.value })}
              disabled={!enabled}
              style={{
                height: 32, padding: "0 10px", fontSize: 13, fontFamily: "var(--font-mono)",
                background: "var(--surface)", border: "1px solid var(--border-strong)",
                borderRadius: 3, color: "var(--ink)", width: 140,
                opacity: enabled ? 1 : 0.5,
              }}
            />
          </div>

          {/* Weekdays */}
          <div>
            <label style={{ fontSize: 12, fontWeight: 500, color: "var(--muted)", textTransform: "uppercase", letterSpacing: "0.06em", display: "block", marginBottom: 6 }}>
              Days of week
            </label>
            <div style={{ display: "flex", gap: 6 }}>
              {DAYS.map((d) => {
                const on = selectedDays.has(d.i);
                return (
                  <button
                    key={d.i}
                    onClick={() => enabled && toggleDay(d.i)}
                    disabled={!enabled}
                    style={{
                      width: 44, height: 32,
                      fontSize: 12, fontWeight: 500,
                      background: on ? "var(--ink)" : "var(--surface)",
                      color: on ? "white" : "var(--muted)",
                      border: "1px solid " + (on ? "var(--ink)" : "var(--border-strong)"),
                      borderRadius: 3,
                      cursor: enabled ? "pointer" : "not-allowed",
                      opacity: enabled ? 1 : 0.5,
                      fontFamily: "var(--font-sans)",
                    }}
                  >
                    {d.label}
                  </button>
                );
              })}
            </div>
          </div>

          {/* Mode */}
          <div>
            <label style={{ fontSize: 12, fontWeight: 500, color: "var(--muted)", textTransform: "uppercase", letterSpacing: "0.06em", display: "block", marginBottom: 6 }}>
              What to run
            </label>
            <div className="chips">
              <button className={`chip${sched.mode === "scan" ? " active" : ""}`} onClick={() => enabled && patch({ mode: "scan" })} disabled={!enabled}>Scan only</button>
              <button className={`chip${sched.mode === "scan+commit" ? " active" : ""}`} onClick={() => enabled && patch({ mode: "scan+commit" })} disabled={!enabled}>Scan + Commit</button>
            </div>
            <div className="mute" style={{ fontSize: 11.5, marginTop: 6 }}>
              {sched.mode === "scan+commit"
                ? "Discover PDFs then copy to network share and delete sources. Destructive."
                : "Discover only. Files stay on source PCs until you manually Commit."}
            </div>
          </div>

          {/* Save */}
          <div style={{ display: "flex", justifyContent: "flex-end", borderTop: "1px solid var(--border)", paddingTop: 14 }}>
            <button className="btn btn-primary" onClick={save} disabled={!dirty || saving}>
              {saving ? "Saving…" : "Save changes"}
            </button>
          </div>
        </div>
      </div>

      {/* Notifications */}
      <div className="card">
        <div className="card-head">
          <div className="card-title">NOTIFICATIONS</div>
        </div>
        <div className="card-body">
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
            <div>
              <div style={{ fontSize: 13, fontWeight: 500 }}>Email report after every commit</div>
              <div className="mute" style={{ fontSize: 12 }}>
                Sends a plain-text summary (files copied, failures, destination) via SMTP.
                Configure creds in <span className="mono">.env</span>: SMTP_HOST, SMTP_USER, SMTP_PASS, EMAIL_FROM, EMAIL_TO.
              </div>
            </div>
            <label style={{ position: "relative", display: "inline-block", width: 40, height: 22 }}>
              <input type="checkbox" checked={!!sched.email_on_commit}
                     onChange={(e) => patch({ email_on_commit: e.target.checked ? 1 : 0 })}
                     style={{ opacity: 0, width: 0, height: 0 }} />
              <span style={{
                position: "absolute", cursor: "pointer", inset: 0,
                background: sched.email_on_commit ? "var(--accent)" : "var(--border-strong)",
                borderRadius: 22, transition: "background 120ms",
              }}>
                <span style={{
                  position: "absolute", top: 2, left: sched.email_on_commit ? 20 : 2,
                  width: 18, height: 18, background: "white", borderRadius: 18,
                  transition: "left 120ms",
                }} />
              </span>
            </label>
          </div>
        </div>
      </div>

      </div>{/* end left column */}
      <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>

      {/* Recent runs — the visible cue that the scheduler actually did something */}
      <div className="card">
        <div className="card-head">
          <div className="card-title">RECENT RUNS</div>
          <div className="mono mute" style={{ fontSize: 11 }}>{runs.length} most recent</div>
        </div>
        {runs.length === 0 ? (
          <div className="empty" style={{ padding: "30px 20px" }}>
            <p>No runs yet. Trigger one manually below or wait for the scheduled time.</p>
          </div>
        ) : (
          <table className="tbl">
            <thead>
              <tr>
                <th style={{ width: 90 }}>Mode</th>
                <th>Started</th>
                <th style={{ width: 90, textAlign: "right" }}>Duration</th>
                <th>Result</th>
              </tr>
            </thead>
            <tbody>
              {runs.map((r) => {
                // ponytail: backend now returns aware ISO timestamps (Postgres port).
                // Feeding them straight to Date() works; the old ".replace(' ','T') + 'Z'"
                // stamped 'Z' onto strings that already had a -04:00 offset and every
                // duration silently became NaN → "running…" forever.
                const started = new Date(r.started_at).getTime();
                const ended = r.ended_at ? new Date(r.ended_at).getTime() : 0;
                const dur = ended ? `${((ended - started) / 1000).toFixed(1)}s` : (r.error ? "failed" : "running…");
                const counts = r.counts || {};
                return (
                  <tr key={r.id}>
                    <td><span className="mono">{r.mode}</span></td>
                    <td className="mono mute">{fmtRelative(r.started_at)} · {fmtDate(r.started_at)}</td>
                    <td className="mono mute" style={{ textAlign: "right" }}>{dur}</td>
                    <td className="mono mute" style={{ fontSize: 12 }}>
                      {r.error
                        ? <span className="pill pill-err">Error</span>
                        : Object.entries(counts).map(([k, v]) => `${k}=${v}`).join("  ")}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>

      <div className="card">
        <div className="card-head">
          <div className="card-title">RUN NOW</div>
        </div>
        <div className="card-body" style={{ display: "flex", gap: 10, alignItems: "center" }}>
          <button className="btn" onClick={() => run("scan")} disabled={!!running}>
            <IconPlay /> Scan now
          </button>
          <button className="btn btn-primary" onClick={() => run("commit")} disabled={!!running}>
            <IconUpload /> Commit now
          </button>
          <div className="spacer" />
          <div className="mute" style={{ fontSize: 12 }}>
            Immediate execution — same as the top-bar buttons.
          </div>
        </div>
      </div>

      </div>{/* end right column */}
      </div>{/* end 2-col grid */}
    </div>
  );
}
