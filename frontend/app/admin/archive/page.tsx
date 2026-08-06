"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { apiFetch } from "../../_lib/auth";
import { RequirePerm } from "../../_components/require-perm";
import { useApp } from "../../_components/app-provider";

// ---------- shared types --------------------------------------------

type Preview = { count: number; sample: string[] };
type SearchResult = {
  id: number;
  host: string;
  filename: string;
  proposed_name: string | null;
  assessment_type: string | null;
  first_name: string | null;
  last_name: string | null;
  committed_at: string | null;
  archived_at: string | null;
  archive_path: string | null;
  dest_path: string | null;
};
type BulkResult = { ok: number; skipped: number; failed: number; errors: { id: number; err: string }[] };
type ArchiveJob = {
  id: string; kind: string;
  before: string | null; after: string | null;
  started_at: string | null; cancelled_at: string | null;
  done: number; total: number | null;
};
type RepairResult = {
  checked: number; fixed: number; applied: boolean;
  counts: Record<string, number>;
  details: { id: number; kind: string; dest_path: string | null; archive_path: string | null }[];
};

// ---------- date helpers --------------------------------------------

const YEAR = new Date().getFullYear();
const PRESETS = [
  { label: `Everything before ${YEAR}`,     iso: `${YEAR}-01-01T00:00:00` },
  { label: `Everything before ${YEAR - 1}`, iso: `${YEAR - 1}-01-01T00:00:00` },
  { label: `Everything before ${YEAR - 2}`, iso: `${YEAR - 2}-01-01T00:00:00` },
  { label: "Older than 90 days",             iso: isoDaysAgo(90) },
  { label: "Older than 1 year",              iso: isoDaysAgo(365) },
  { label: "Custom range…",                  iso: "" },
];

function isoDaysAgo(days: number): string {
  const d = new Date();
  d.setDate(d.getDate() - days);
  return d.toISOString().slice(0, 10) + "T00:00:00";
}

function fmtWhen(s: string | null): string {
  if (!s) return "—";
  const d = new Date(s);
  return d.toLocaleString();
}

// ---------- SSE consumer --------------------------------------------

type SseFrame =
  | { phase: "start"; total: number }
  | { phase: "progress"; done: number; total: number; batch_ms: number; batch_ok: number; batch_fail: number; errors: { id: number; err: string }[] }
  | { phase: "paused"; reason: string; done: number; total: number }
  | { phase: "cancelled"; done: number; total: number }
  | { phase: "done"; done: number; total: number; ok: number; skipped: number; failed: number };

async function consumeSse(
  url: string,
  body: unknown,
  onFrame: (f: SseFrame) => void,
  onJobId?: (id: string) => void,
): Promise<void> {
  const r = await apiFetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const jobId = r.headers.get("X-Job-Id");
  if (jobId && onJobId) onJobId(jobId);
  if (!r.body) throw new Error("no stream");
  const reader = r.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    const parts = buf.split("\n\n");
    buf = parts.pop() ?? "";
    for (const p of parts) {
      if (!p.startsWith("data: ")) continue;
      const text = p.slice(6);
      if (text === "[DONE]") continue;
      try {
        onFrame(JSON.parse(text) as SseFrame);
      } catch {
        // ignore malformed frames
      }
    }
  }
}

// ---------- entry --------------------------------------------------

export default function AdminArchivePage() {
  return <RequirePerm perms={["pdf:archive"]}><Inner /></RequirePerm>;
}

function Inner() {
  return (
    <div style={{ padding: "20px 28px", display: "flex", flexDirection: "column", gap: 20 }}>
      <div>
        <h1 style={{ fontSize: 20, fontWeight: 600, margin: 0 }}>Archive</h1>
        <p className="mute" style={{ fontSize: 12.5, margin: "4px 0 0 0" }}>
          Move committed PDFs into <span className="mono">_Archive/</span> on the share and
          restore them on request. Files stay browsable, viewable, and reversible.
        </p>
      </div>

      <SearchPanel />
      <BulkDateOpPanel kind="archive" />
      <BulkDateOpPanel kind="restore" />
      <RecentOpsPanel />
      <RepairPanel />
    </div>
  );
}

// ---------- Search & restore panel -----------------------------------

function SearchPanel() {
  const { refresh } = useApp();
  const [first, setFirst] = useState("");
  const [last, setLast] = useState("");
  const [filename, setFilename] = useState("");
  const [assessment, setAssessment] = useState("");
  const [after, setAfter] = useState("");
  const [before, setBefore] = useState("");
  const [dateField, setDateField] = useState<"committed_at" | "archived_at">("committed_at");
  const [results, setResults] = useState<SearchResult[]>([]);
  const [total, setTotal] = useState(0);
  const [checked, setChecked] = useState<Set<number>>(new Set());
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);

  const anyField = !!(first || last || filename || assessment || after || before);

  const search = useCallback(async () => {
    if (!anyField) return;
    setLoading(true);
    setErr(null);
    setMsg(null);
    setChecked(new Set());
    try {
      const body: Record<string, unknown> = { limit: 100, date_field: dateField };
      if (first) body.first_name = first;
      if (last) body.last_name = last;
      if (filename) body.filename = filename;
      if (assessment) body.assessment_type = assessment;
      if (after) body.after = after + "T00:00:00";
      if (before) body.before = before + "T23:59:59";
      const r = await apiFetch("/api/pdfs/archive-search", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!r.ok) {
        const b = await r.json().catch(() => ({}));
        throw new Error(b.detail ?? `HTTP ${r.status}`);
      }
      const data = await r.json();
      setResults(data.results);
      setTotal(data.total);
    } catch (e) {
      setErr(String(e instanceof Error ? e.message : e));
    } finally {
      setLoading(false);
    }
  }, [anyField, first, last, filename, assessment, after, before, dateField]);

  // Debounced live search.
  useEffect(() => {
    if (!anyField) { setResults([]); setTotal(0); return; }
    const t = setTimeout(search, 400);
    return () => clearTimeout(t);
  }, [search, anyField]);

  const toggle = (id: number) => setChecked((prev) => {
    const next = new Set(prev);
    if (next.has(id)) next.delete(id); else next.add(id);
    return next;
  });
  const toggleAll = () => setChecked((prev) => {
    if (prev.size === results.length) return new Set();
    return new Set(results.map((r) => r.id));
  });

  const restoreOne = async (id: number) => {
    setMsg(null);
    const r = await apiFetch("/api/pdfs/bulk/restore", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ids: [id] }),
    });
    const data: BulkResult = await r.json();
    if (data.failed) setErr(`Restore failed: ${data.errors[0]?.err ?? "unknown"}`);
    else { setMsg("Restored."); setResults((prev) => prev.filter((r) => r.id !== id)); }
    refresh();
  };

  const restoreSelected = async () => {
    if (!checked.size) return;
    setMsg(null);
    const ids = [...checked];
    const r = await apiFetch("/api/pdfs/bulk/restore", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ids }),
    });
    const data: BulkResult = await r.json();
    setResults((prev) => prev.filter((r) => !checked.has(r.id) || data.errors.some((e) => e.id === r.id)));
    setChecked(new Set());
    if (data.failed) setErr(`${data.ok}/${ids.length} restored, ${data.failed} failed.`);
    else setMsg(`${data.ok} restored.`);
    refresh();
  };

  return (
    <section className="card" style={{ padding: 20 }}>
      <h2 style={{ fontSize: 15, fontWeight: 600, margin: "0 0 4px 0" }}>Find archived file</h2>
      <p className="mute" style={{ fontSize: 12, margin: "0 0 14px 0" }}>
        Primary flow for client requests. At least one field required; Restore is per-row or per-selection.
      </p>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))", gap: 10, marginBottom: 12 }}>
        <Field label="First name"  value={first}      onChange={setFirst}      placeholder="jane" />
        <Field label="Last name"   value={last}       onChange={setLast}       placeholder="doe" />
        <Field label="Filename"    value={filename}   onChange={setFilename}   placeholder="partial ok" />
        <Field label="Assessment"  value={assessment} onChange={setAssessment} placeholder="e.g. O_NET_Interest_Profiler" />
        <Field label="From"        value={after}      onChange={setAfter}      type="date" />
        <Field label="To"          value={before}     onChange={setBefore}     type="date" />
      </div>

      <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 12, fontSize: 12 }}>
        <span className="mute">Filter on:</span>
        <label style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <input type="radio" checked={dateField === "committed_at"} onChange={() => setDateField("committed_at")} />
          Committed
        </label>
        <label style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <input type="radio" checked={dateField === "archived_at"} onChange={() => setDateField("archived_at")} />
          Archived
        </label>
      </div>

      {err && <div className="mono" style={{ color: "var(--err)", fontSize: 12, marginBottom: 10 }}>{err}</div>}
      {msg && <div className="mono" style={{ color: "var(--ok, #6c6)", fontSize: 12, marginBottom: 10 }}>{msg}</div>}

      {!anyField ? (
        <p className="mute" style={{ fontSize: 12.5, margin: 0 }}>Enter a name, filename, assessment, or date range to search.</p>
      ) : loading ? (
        <p className="mute" style={{ fontSize: 12.5, margin: 0 }}>Searching…</p>
      ) : results.length === 0 ? (
        <p className="mute" style={{ fontSize: 12.5, margin: 0 }}>No archived files match your search.</p>
      ) : (
        <>
          <div style={{ fontSize: 12.5, marginBottom: 8 }}>
            <strong>{total}</strong> matching archived file{total === 1 ? "" : "s"}
            {total > results.length && <span className="mute"> (showing first {results.length})</span>}
          </div>
          <div style={{ border: "1px solid var(--border)", borderRadius: 3, overflow: "hidden" }}>
            <table className="tbl">
              <thead>
                <tr>
                  <th className="check">
                    <input type="checkbox" checked={checked.size === results.length && results.length > 0}
                      onChange={toggleAll} aria-label="Select all" />
                  </th>
                  <th>Filename</th>
                  <th>Assessment</th>
                  <th>Committed</th>
                  <th>Archived</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {results.map((row) => (
                  <tr key={row.id}>
                    <td className="check">
                      <input type="checkbox" checked={checked.has(row.id)} onChange={() => toggle(row.id)} />
                    </td>
                    <td className="mono">{row.proposed_name ?? row.filename}</td>
                    <td className="mute" style={{ fontSize: 12 }}>{(row.assessment_type ?? "—").replace(/_/g, " ")}</td>
                    <td className="mono mute" style={{ fontSize: 11.5 }}>{fmtWhen(row.committed_at)}</td>
                    <td className="mono mute" style={{ fontSize: 11.5 }}>{fmtWhen(row.archived_at)}</td>
                    <td style={{ textAlign: "right" }}>
                      <button className="btn" onClick={() => restoreOne(row.id)}>Restore</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div style={{ display: "flex", justifyContent: "flex-end", marginTop: 10 }}>
            <button className="btn" disabled={!checked.size} onClick={restoreSelected}>
              Restore selected ({checked.size})
            </button>
          </div>
        </>
      )}
    </section>
  );
}

// ---------- Bulk-by-date panel (archive / restore) -------------------

function BulkDateOpPanel({ kind }: { kind: "archive" | "restore" }) {
  const { refresh } = useApp();
  const [preset, setPreset] = useState(PRESETS[0].label);
  const [before, setBefore] = useState(PRESETS[0].iso.slice(0, 10));
  const [after, setAfter] = useState("");
  const [dateField, setDateField] = useState<"committed_at" | "archived_at">(
    kind === "archive" ? "committed_at" : "archived_at",
  );
  const [preview, setPreview] = useState<Preview | null>(null);
  const [previewErr, setPreviewErr] = useState<string | null>(null);
  const [previewing, setPreviewing] = useState(false);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [running, setRunning] = useState(false);
  const [progress, setProgress] = useState<{ done: number; total: number; batch_ms?: number } | null>(null);
  const [errs, setErrs] = useState<{ id: number; err: string }[]>([]);
  const [finalMsg, setFinalMsg] = useState<string | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const [cancelling, setCancelling] = useState(false);

  const isCustom = preset === "Custom range…";
  const previewBody = useMemo(() => {
    const body: Record<string, string> = { before: before + "T23:59:59" };
    if (after) body.after = after + "T00:00:00";
    if (kind === "restore") body.date_field = dateField;
    return body;
  }, [before, after, kind, dateField]);

  // Live preview count (debounced) whenever the cutoff changes.
  useEffect(() => {
    if (!before) { setPreview(null); setPreviewErr(null); return; }
    setPreviewing(true);
    setPreviewErr(null);
    const t = setTimeout(async () => {
      try {
        const r = await apiFetch(`/api/pdfs/${kind}-preview`, {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify(previewBody),
        });
        const data = await r.json();
        if (!r.ok) {
          // Surface the real error instead of silently showing "0 eligible".
          setPreview(null);
          setPreviewErr(`HTTP ${r.status}: ${data?.detail ?? data?.title ?? JSON.stringify(data)}`);
        } else if (typeof data?.count !== "number") {
          setPreview(null);
          setPreviewErr(`Unexpected response shape: ${JSON.stringify(data).slice(0, 200)}`);
        } else {
          setPreview(data as Preview);
        }
      } catch (e) {
        setPreview(null);
        setPreviewErr(String(e instanceof Error ? e.message : e));
      } finally {
        setPreviewing(false);
      }
    }, 400);
    return () => clearTimeout(t);
  }, [before, previewBody, kind]);

  const changePreset = (label: string) => {
    setPreset(label);
    const p = PRESETS.find((p) => p.label === label);
    if (p && p.iso) setBefore(p.iso.slice(0, 10));
  };

  const runIt = async () => {
    setConfirmOpen(false);
    setRunning(true);
    setProgress(null);
    setErrs([]);
    setFinalMsg(null);
    setJobId(null);
    try {
      await consumeSse(
        `/api/pdfs/${kind}-by-date`,
        previewBody,
        (frame) => {
          if (frame.phase === "start") {
            setProgress({ done: 0, total: frame.total });
          } else if (frame.phase === "progress") {
            setProgress({ done: frame.done, total: frame.total, batch_ms: frame.batch_ms });
            if (frame.errors?.length) {
              setErrs((prev) => [...prev, ...frame.errors].slice(-40));
            }
          } else if (frame.phase === "paused") {
            setFinalMsg(`Paused: ${frame.reason}`);
          } else if (frame.phase === "cancelled") {
            setFinalMsg(`Cancelled at ${frame.done}/${frame.total}`);
          } else if (frame.phase === "done") {
            setFinalMsg(`${frame.ok} ${kind}d · ${frame.skipped} skipped · ${frame.failed} failed`);
          }
        },
        (id) => setJobId(id),
      );
    } catch (e) {
      setFinalMsg(`Error: ${String(e)}`);
    } finally {
      setRunning(false);
      refresh();
    }
  };

  const cancel = async () => {
    if (!jobId) return;
    setCancelling(true);
    try {
      await apiFetch(`/api/pdfs/archive-jobs/${jobId}/cancel`, { method: "POST" });
    } finally {
      setCancelling(false);
    }
  };

  const verb = kind === "archive" ? "Archive" : "Restore";
  const pct = progress && progress.total ? Math.floor((progress.done / progress.total) * 100) : 0;

  return (
    <section className="card" style={{ padding: 20 }}>
      <h2 style={{ fontSize: 15, fontWeight: 600, margin: "0 0 4px 0" }}>Bulk {verb.toLowerCase()} by date</h2>
      <p className="mute" style={{ fontSize: 12, margin: "0 0 14px 0" }}>
        {kind === "archive"
          ? "Moves every committed PDF older than the cutoff into _Archive/. Streams progress; cancel any time."
          : "Restores archived PDFs matching the date range. Symmetric with archive."}
      </p>

      <div style={{ display: "grid", gridTemplateColumns: "220px 160px auto", gap: 10, alignItems: "end", marginBottom: 12 }}>
        <label>
          <div style={{ fontSize: 11, color: "var(--muted)", marginBottom: 4 }}>Preset</div>
          <select value={preset} onChange={(e) => changePreset(e.target.value)}
                  style={{ width: "100%", height: 30, padding: "0 8px", fontSize: 12.5, background: "var(--surface)", border: "1px solid var(--border-strong)", borderRadius: 3, color: "var(--ink)" }}>
            {PRESETS.map((p) => <option key={p.label} value={p.label}>{p.label}</option>)}
          </select>
        </label>
        <label>
          <div style={{ fontSize: 11, color: "var(--muted)", marginBottom: 4 }}>Cutoff (before)</div>
          <input type="date" value={before} onChange={(e) => setBefore(e.target.value)}
                 style={{ width: "100%", height: 30, padding: "0 8px", fontSize: 12.5, background: "var(--surface)", border: "1px solid var(--border-strong)", borderRadius: 3, color: "var(--ink)" }} />
        </label>
        {isCustom && (
          <label>
            <div style={{ fontSize: 11, color: "var(--muted)", marginBottom: 4 }}>From (after)</div>
            <input type="date" value={after} onChange={(e) => setAfter(e.target.value)}
                   style={{ width: 160, height: 30, padding: "0 8px", fontSize: 12.5, background: "var(--surface)", border: "1px solid var(--border-strong)", borderRadius: 3, color: "var(--ink)" }} />
          </label>
        )}
      </div>

      {kind === "restore" && (
        <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 12, fontSize: 12 }}>
          <span className="mute">Cutoff applies to:</span>
          <label style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <input type="radio" checked={dateField === "archived_at"} onChange={() => setDateField("archived_at")} />
            Archived date
          </label>
          <label style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <input type="radio" checked={dateField === "committed_at"} onChange={() => setDateField("committed_at")} />
            Committed date
          </label>
        </div>
      )}

      <div style={{ padding: "10px 12px", background: "var(--bg)", border: "1px solid var(--border)", borderRadius: 3, marginBottom: 12 }}>
        {previewErr ? (
          <div className="mono" style={{ color: "var(--err)", fontSize: 12 }}>Preview failed — {previewErr}</div>
        ) : (
          <>
            <div style={{ fontSize: 13 }}>
              → <strong>{previewing ? "…" : (preview?.count ?? 0).toLocaleString()}</strong> file{(preview?.count ?? 0) === 1 ? "" : "s"} eligible
            </div>
            {preview && preview.sample.length > 0 && (
              <div className="mono mute" style={{ fontSize: 11, marginTop: 6, wordBreak: "break-all" }}>
                Sample: {preview.sample.slice(0, 5).join(", ")}
                {preview.sample.length > 5 && ` … +${preview.sample.length - 5} more`}
              </div>
            )}
          </>
        )}
      </div>

      {running && !progress && (
        <div style={{ marginBottom: 12, padding: "10px 12px", background: "var(--bg)", border: "1px solid var(--border)", borderRadius: 3, fontSize: 12.5 }}>
          <span>Starting… </span>
          <span className="mute">Waiting for the first batch to finish (~2s per 100 files).</span>
        </div>
      )}
      {progress && (
        <div style={{ marginBottom: 12 }}>
          <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12, marginBottom: 4 }}>
            <span>{progress.done.toLocaleString()} / {progress.total.toLocaleString()} ({pct}%)</span>
            {progress.batch_ms !== undefined && <span className="mute">last batch: {progress.batch_ms}ms</span>}
          </div>
          <div style={{ background: "var(--bg)", height: 6, borderRadius: 3, overflow: "hidden" }}>
            <div style={{ width: `${pct}%`, height: "100%", background: "var(--accent, #3b82f6)", transition: "width 200ms" }} />
          </div>
        </div>
      )}

      {errs.length > 0 && (
        <details style={{ marginBottom: 12, fontSize: 12 }}>
          <summary style={{ cursor: "pointer" }}>Errors ({errs.length})</summary>
          <div className="mono" style={{ maxHeight: 160, overflow: "auto", padding: 8, background: "var(--bg)", borderRadius: 3, marginTop: 6, fontSize: 11 }}>
            {errs.map((e, i) => <div key={i}>#{e.id}: {e.err}</div>)}
          </div>
        </details>
      )}

      {finalMsg && (
        <div className="mono" style={{ fontSize: 12, marginBottom: 12, color: finalMsg.startsWith("Error") || finalMsg.startsWith("Paused") ? "var(--err)" : "var(--ink)" }}>
          {finalMsg}
        </div>
      )}

      <div style={{ display: "flex", gap: 8 }}>
        {running ? (
          <button className="btn" onClick={cancel} disabled={cancelling || !jobId}>
            {cancelling ? "Cancelling…" : "Cancel"}
          </button>
        ) : (
          <button className="btn" onClick={() => setConfirmOpen(true)} disabled={!preview || preview.count === 0}>
            {verb} {preview?.count ? preview.count.toLocaleString() : ""} file{preview?.count === 1 ? "" : "s"}
          </button>
        )}
      </div>

      {confirmOpen && preview && (
        <div style={{
          position: "fixed", inset: 0, background: "rgba(0,0,0,0.4)", display: "grid", placeItems: "center", zIndex: 50,
        }} onClick={() => setConfirmOpen(false)}>
          <div className="card" style={{ padding: 20, minWidth: 400, maxWidth: 500 }} onClick={(e) => e.stopPropagation()}>
            <h3 style={{ margin: "0 0 8px 0", fontSize: 15 }}>Confirm {verb.toLowerCase()}</h3>
            <p style={{ fontSize: 13, margin: "0 0 16px 0" }}>
              About to {verb.toLowerCase()} <strong>{preview.count.toLocaleString()}</strong> file(s).
              {kind === "archive" ? " Restore is per-file or per-selection — no single-click bulk undo." : ""}
            </p>
            <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
              <button className="btn" onClick={() => setConfirmOpen(false)}>Cancel</button>
              <button className="btn" onClick={runIt}>Continue</button>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}

// ---------- Recent operations table ----------------------------------

function RecentOpsPanel() {
  const [jobs, setJobs] = useState<ArchiveJob[]>([]);

  const load = useCallback(async () => {
    try {
      const r = await apiFetch("/api/pdfs/archive-jobs");
      const data = await r.json();
      setJobs(data.jobs);
    } catch { /* ignore */ }
  }, []);

  useEffect(() => {
    load();
    const t = setInterval(load, 3000);
    return () => clearInterval(t);
  }, [load]);

  return (
    <section className="card" style={{ padding: 20 }}>
      <h2 style={{ fontSize: 15, fontWeight: 600, margin: "0 0 12px 0" }}>Running / recent operations</h2>
      {jobs.length === 0 ? (
        <p className="mute" style={{ fontSize: 12.5, margin: 0 }}>No archive/restore ops recorded this session.</p>
      ) : (
        <div style={{ border: "1px solid var(--border)", borderRadius: 3, overflow: "hidden" }}>
          <table className="tbl">
            <thead>
              <tr>
                <th>Started</th>
                <th>Kind</th>
                <th>Range</th>
                <th>Progress</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {jobs.map((j) => {
                const running = !j.cancelled_at && (j.total === null || j.done < (j.total ?? 0));
                const pct = j.total ? Math.floor((j.done / j.total) * 100) : 0;
                return (
                  <tr key={j.id}>
                    <td className="mono mute" style={{ fontSize: 11.5 }}>{fmtWhen(j.started_at)}</td>
                    <td>{j.kind}</td>
                    <td className="mono mute" style={{ fontSize: 11.5 }}>
                      {j.before ? new Date(j.before).toLocaleDateString() : "—"}
                      {j.after && ` (from ${new Date(j.after).toLocaleDateString()})`}
                    </td>
                    <td className="mono" style={{ fontSize: 12 }}>
                      {j.done.toLocaleString()} / {j.total === null ? "?" : j.total.toLocaleString()} ({pct}%)
                    </td>
                    <td>
                      <span className={`pill ${j.cancelled_at ? "" : running ? "pill-warn" : "pill-ok"}`}>
                        {j.cancelled_at ? "cancelled" : running ? "running" : "complete"}
                      </span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

// ---------- Repair panel --------------------------------------------

function RepairPanel() {
  const [open, setOpen] = useState(false);
  const [result, setResult] = useState<RepairResult | null>(null);
  const [scanning, setScanning] = useState(false);
  const [applying, setApplying] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const scan = async () => {
    setScanning(true);
    setErr(null);
    try {
      const r = await apiFetch("/api/pdfs/repair-check", { method: "POST" });
      setResult(await r.json());
    } catch (e) {
      setErr(String(e));
    } finally {
      setScanning(false);
    }
  };

  const apply = async () => {
    if (!confirm("Apply all detected fixes? This updates rows and may delete duplicate files.")) return;
    setApplying(true);
    setErr(null);
    try {
      const r = await apiFetch("/api/pdfs/repair-apply", { method: "POST" });
      setResult(await r.json());
    } catch (e) {
      setErr(String(e));
    } finally {
      setApplying(false);
    }
  };

  const hasFixable = result && Object.entries(result.counts).some(
    ([k, v]) => k !== "ok" && k !== "active_missing" && k !== "active_stale_archive_copy" && v > 0,
  );

  return (
    <section className="card" style={{ padding: 20 }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <div>
          <h2 style={{ fontSize: 15, fontWeight: 600, margin: 0 }}>Repair</h2>
          <p className="mute" style={{ fontSize: 12, margin: "4px 0 0 0" }}>
            Reconcile pdfs table state against files on the share.
          </p>
        </div>
        <button className="btn" onClick={() => setOpen((v) => !v)}>{open ? "Hide" : "Show"}</button>
      </div>

      {open && (
        <div style={{ marginTop: 14 }}>
          <div style={{ display: "flex", gap: 8, marginBottom: 12 }}>
            <button className="btn" onClick={scan} disabled={scanning || applying}>
              {scanning ? "Scanning…" : "Run repair scan"}
            </button>
            {hasFixable && (
              <button className="btn" onClick={apply} disabled={scanning || applying}>
                {applying ? "Applying…" : "Apply repairs"}
              </button>
            )}
          </div>
          {err && <div className="mono" style={{ color: "var(--err)", fontSize: 12, marginBottom: 8 }}>{err}</div>}
          {result && (
            <>
              <div className="mono" style={{ fontSize: 12, marginBottom: 8 }}>
                Checked {result.checked} rows · Fixed {result.fixed} {result.applied ? "(applied)" : "(dry-run)"}
              </div>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))", gap: 8, marginBottom: 12 }}>
                {Object.entries(result.counts).map(([kind, n]) => (
                  <div key={kind} style={{ padding: "8px 10px", background: "var(--bg)", borderRadius: 3, fontSize: 12 }}>
                    <div className="mono mute" style={{ fontSize: 11 }}>{kind}</div>
                    <div style={{ fontSize: 15, fontWeight: 600 }}>{n}</div>
                  </div>
                ))}
              </div>
              {result.details.length > 0 && (
                <details>
                  <summary style={{ cursor: "pointer", fontSize: 12 }}>Details ({result.details.length})</summary>
                  <div className="mono" style={{ maxHeight: 220, overflow: "auto", padding: 8, background: "var(--bg)", borderRadius: 3, marginTop: 6, fontSize: 11 }}>
                    {result.details.map((d) => (
                      <div key={d.id}>#{d.id} <span className="mute">[{d.kind}]</span> {d.archive_path ?? d.dest_path}</div>
                    ))}
                  </div>
                </details>
              )}
            </>
          )}
        </div>
      )}
    </section>
  );
}

// ---------- reusable field ------------------------------------------

function Field({ label, value, onChange, placeholder, type = "text" }: {
  label: string; value: string; onChange: (s: string) => void;
  placeholder?: string; type?: string;
}) {
  return (
    <label>
      <div style={{ fontSize: 11, color: "var(--muted)", marginBottom: 4 }}>{label}</div>
      <input type={type} value={value} onChange={(e) => onChange(e.target.value)} placeholder={placeholder}
             style={{
               width: "100%", height: 30, padding: "0 10px", fontSize: 12.5,
               background: "var(--surface)", border: "1px solid var(--border-strong)",
               borderRadius: 3, color: "var(--ink)", fontFamily: "var(--font-sans)",
             }} />
    </label>
  );
}
