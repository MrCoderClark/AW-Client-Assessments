"use client";

import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { apiFetch, getAccessToken } from "../_lib/auth";
import { useAuth } from "./auth-provider";

export type Pdf = {
  id: number;
  host: string;
  source_path: string;
  filename: string;
  proposed_name: string | null;
  assessment_type: string | null;
  first_name: string | null;
  last_name: string | null;
  size: number;
  mtime: string;
  md5: string;
  indexed_at: string;
  committed_at: string | null;
  dest_path: string | null;
  archived_at: string | null;
  archive_path: string | null;
  archive_status: string | null;
};

export type LogLine = { text: string; kind: "hdr" | "ok" | "warn" | "err" | "info" };
export type RunKind = "scan" | "commit";

export type BulkAction = "commit" | "delete";
export type ArchivedView = "false" | "true" | "all";
export type BulkArchiveResult = { ok: number; skipped: number; failed: number; errors: { id: number; err: string }[] };

type AppState = {
  // Always the ACTIVE (non-archived) list — used by Dashboard, Recent Files,
  // Command Palette. Callers that need archived rows (Files page in
  // Archived/All view, Admin archive page) fetch their own slice.
  pdfs: Pdf[];
  loading: boolean;
  apiReachable: boolean | null;
  refresh: () => Promise<void>;
  // Incremented on every archive/restore so consumers with their own
  // lists (Files page) can re-fetch without wiring bespoke callbacks.
  mutationVersion: number;
  log: LogLine[];
  clearLog: () => void;
  running: false | RunKind | "bulk";
  run: (kind: RunKind) => void;
  runBulk: (action: BulkAction, ids: number[], opts?: { deleteFiles?: boolean }) => void;
  runArchive: (ids: number[]) => Promise<BulkArchiveResult>;
  runRestore: (ids: number[]) => Promise<BulkArchiveResult>;
  drawerOpen: boolean;
  setDrawerOpen: (open: boolean) => void;
};

const Ctx = createContext<AppState | null>(null);

function classifyLine(text: string): LogLine["kind"] {
  const t = text.toLowerCase();
  if (t.includes("[ok]") || t.includes("[new]") || t.includes("[update]") || t.includes("[dup]")) return "ok";
  if (t.includes("[skip") || t.includes("[unlock]") || t.includes("locked") || t.includes("unreachable") || t.includes("warn")) return "warn";
  if (t.includes("[fail]") || t.includes("[verify-fail]") || t.includes("error")) return "err";
  if (/^pc\d+/i.test(text.trim()) || text.startsWith("Window:") || text.startsWith("Destination:") || text.startsWith("PCs:")) return "hdr";
  return "info";
}

async function streamAction(url: string, onLine: (line: LogLine) => void, body?: unknown): Promise<void> {
  const r = await apiFetch(url, {
    method: "POST",
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
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
      onLine({ text, kind: classifyLine(text) });
    }
  }
}

export function AppProvider({ children }: { children: React.ReactNode }) {
  const [pdfs, setPdfs] = useState<Pdf[]>([]);
  const [loading, setLoading] = useState(true);
  const [apiReachable, setApiReachable] = useState<boolean | null>(null);
  const [log, setLog] = useState<LogLine[]>([]);
  const [running, setRunning] = useState<false | RunKind | "bulk">(false);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [mutationVersion, setMutationVersion] = useState(0);

  const { status } = useAuth();

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      // Provider list is ALWAYS the active slice — see AppState.pdfs comment.
      const r = await apiFetch(`/api/pdfs?archived=false`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data = await r.json();
      setPdfs(data);
      setApiReachable(true);
    } catch {
      setApiReachable(false);
    } finally {
      setLoading(false);
    }
  }, []);

  // Only fetch data once we're authenticated — no point 401ing on boot.
  useEffect(() => {
    if (status === "authenticated") refresh();
  }, [status, refresh]);

  const clearLog = useCallback(() => setLog([]), []);

  const run = useCallback((kind: RunKind) => {
    setLog([]);
    setRunning(kind);
    setDrawerOpen(true);
    const url = `/api/${kind === "scan" ? "scans" : "commits"}`;
    (async () => {
      try {
        await streamAction(url, (line) => setLog((prev) => [...prev, line]));
      } catch (e) {
        setLog((prev) => [...prev, { text: `[error] ${String(e)}`, kind: "err" }]);
      } finally {
        setRunning(false);
        refresh();
      }
    })();
  }, [refresh]);

  const runBulk = useCallback((action: BulkAction, ids: number[], opts?: { deleteFiles?: boolean }) => {
    if (!ids.length) return;
    setLog([]);
    setRunning("bulk");
    setDrawerOpen(true);
    (async () => {
      try {
        await streamAction(
          `/api/pdfs/bulk`,
          (line) => setLog((prev) => [...prev, line]),
          { action, ids, delete_files: !!opts?.deleteFiles },
        );
      } catch (e) {
        setLog((prev) => [...prev, { text: `[error] ${String(e)}`, kind: "err" }]);
      } finally {
        setRunning(false);
        refresh();
      }
    })();
  }, [refresh]);

  const runArchive = useCallback(async (ids: number[]): Promise<BulkArchiveResult> => {
    if (!ids.length) return { ok: 0, skipped: 0, failed: 0, errors: [] };
    const r = await apiFetch(`/api/pdfs/bulk/archive`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ids }),
    });
    const result: BulkArchiveResult = await r.json();
    await refresh();
    setMutationVersion((v) => v + 1);
    return result;
  }, [refresh]);

  const runRestore = useCallback(async (ids: number[]): Promise<BulkArchiveResult> => {
    if (!ids.length) return { ok: 0, skipped: 0, failed: 0, errors: [] };
    const r = await apiFetch(`/api/pdfs/bulk/restore`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ids }),
    });
    const result: BulkArchiveResult = await r.json();
    await refresh();
    setMutationVersion((v) => v + 1);
    return result;
  }, [refresh]);

  return (
    <Ctx.Provider value={{
      pdfs, loading, apiReachable, refresh,
      mutationVersion,
      log, clearLog, running, run, runBulk,
      runArchive, runRestore,
      drawerOpen, setDrawerOpen,
    }}>
      {children}
    </Ctx.Provider>
  );
}

export function useApp() {
  const v = useContext(Ctx);
  if (!v) throw new Error("useApp outside AppProvider");
  return v;
}
