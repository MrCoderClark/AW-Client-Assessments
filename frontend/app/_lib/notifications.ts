// Notification store: initial fetch + SSE stream + unread counter.
//
// SSE via fetch+ReadableStream (not EventSource) so we can send the Bearer
// header. Auto-reconnects with backoff and passes `since_id` on reconnect
// to catch anything that happened while offline.

import { apiFetch, getAccessToken } from "./auth";

export type Notification = {
  id: string;
  category: "scan_commit" | "security" | "user_lifecycle" | "pc_health";
  kind: string;
  severity: "INFO" | "WARN" | "SEC";
  title: string;
  body: string;
  url: string | null;
  context: Record<string, unknown>;
  read_at: string | null;
  created_at: string;
};

type Listener = () => void;

type State = {
  items: Notification[];
  unread: number;
  connected: boolean;
};

const MAX_ITEMS = 100;

let state: State = { items: [], unread: 0, connected: false };
const listeners = new Set<Listener>();
let started = false;
let abort: AbortController | null = null;
let reconnectTimer: ReturnType<typeof setTimeout> | null = null;

function set(next: Partial<State>) {
  state = { ...state, ...next };
  listeners.forEach((l) => l());
}

export function subscribe(l: Listener): () => void {
  listeners.add(l);
  return () => { listeners.delete(l); };
}

export function snapshot(): State {
  return state;
}

async function fetchInitial(sinceId?: string): Promise<void> {
  const qs = sinceId ? `?since_id=${encodeURIComponent(sinceId)}` : "";
  const r = await apiFetch(`/api/v1/notifications${qs}`);
  if (!r.ok) return;
  const j = await r.json();
  const incoming: Notification[] = j.notifications ?? [];
  // Merge: dedupe on id, newest first, cap at MAX_ITEMS.
  const seen = new Set(state.items.map((x) => x.id));
  const merged = [
    ...incoming.filter((x) => !seen.has(x.id)),
    ...state.items,
  ]
    .sort((a, b) => (a.created_at < b.created_at ? 1 : -1))
    .slice(0, MAX_ITEMS);
  set({ items: merged, unread: j.unread ?? merged.filter((x) => !x.read_at).length });
}

// ---- history query (used by /notifications page) --------------------

export type ListParams = {
  limit?: number;
  offset?: number;
  category?: Notification["category"];
  severity?: Notification["severity"];
  unreadOnly?: boolean;
};

export type ListResponse = {
  notifications: Notification[];
  total: number;
  unread: number;
  limit: number;
  offset: number;
};

export async function listHistory(params: ListParams = {}): Promise<ListResponse> {
  const qs = new URLSearchParams();
  qs.set("limit", String(params.limit ?? 50));
  qs.set("offset", String(params.offset ?? 0));
  if (params.category) qs.set("category", params.category);
  if (params.severity) qs.set("severity", params.severity);
  if (params.unreadOnly) qs.set("unread_only", "true");
  const r = await apiFetch(`/api/v1/notifications?${qs.toString()}`);
  if (!r.ok) {
    const j = await r.json().catch(() => ({}));
    throw new Error(j.detail || j.title || `HTTP ${r.status}`);
  }
  return r.json();
}

async function runStream(): Promise<void> {
  const token = getAccessToken();
  if (!token) return;
  abort = new AbortController();
  let buf = "";
  try {
    const r = await fetch("/api/v1/notifications/stream", {
      headers: { Authorization: `Bearer ${token}`, Accept: "text/event-stream" },
      credentials: "include",
      signal: abort.signal,
    });
    if (!r.ok || !r.body) {
      set({ connected: false });
      return;
    }
    set({ connected: true });
    const reader = r.body.getReader();
    const decoder = new TextDecoder();
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      // Split on SSE frame boundary (double newline).
      let idx: number;
      while ((idx = buf.indexOf("\n\n")) !== -1) {
        const frame = buf.slice(0, idx);
        buf = buf.slice(idx + 2);
        handleFrame(frame);
      }
    }
  } catch {
    // aborted or network drop — outer loop will reconnect
  } finally {
    set({ connected: false });
  }
}

function handleFrame(frame: string): void {
  // Ignore comment frames (SSE keepalives start with ':')
  const lines = frame.split("\n");
  let event = "message";
  let data = "";
  for (const ln of lines) {
    if (ln.startsWith(":")) continue;
    if (ln.startsWith("event:")) event = ln.slice(6).trim();
    else if (ln.startsWith("data:")) data += ln.slice(5).trim();
  }
  if (event !== "notification" || !data) return;
  try {
    const n: Notification = JSON.parse(data);
    // Prepend + dedupe + cap
    const items = [n, ...state.items.filter((x) => x.id !== n.id)].slice(0, MAX_ITEMS);
    const unread = items.filter((x) => !x.read_at).length;
    set({ items, unread });
  } catch {
    /* ignore */
  }
}

export function start(): void {
  if (started) return;
  started = true;
  void (async () => {
    await fetchInitial();
    let backoffMs = 1000;
    while (started) {
      await runStream();
      if (!started) break;
      // Catch up on anything missed while disconnected
      const newest = state.items[0]?.id;
      try { await fetchInitial(newest); } catch { /* ignore */ }
      await new Promise((res) => {
        reconnectTimer = setTimeout(res, backoffMs);
      });
      backoffMs = Math.min(backoffMs * 2, 30_000);
    }
  })();
}

export function stop(): void {
  started = false;
  if (abort) { abort.abort(); abort = null; }
  if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null; }
  set({ items: [], unread: 0, connected: false });
}

export async function markRead(id: string): Promise<void> {
  const r = await apiFetch(`/api/v1/notifications/${id}/read`, { method: "POST" });
  if (r.ok || r.status === 404) {
    const items = state.items.map((x) => x.id === id && !x.read_at
      ? { ...x, read_at: new Date().toISOString() } : x);
    set({ items, unread: items.filter((x) => !x.read_at).length });
  }
}

export async function markAllRead(): Promise<void> {
  const r = await apiFetch(`/api/v1/notifications/read-all`, { method: "POST" });
  if (r.ok) {
    const now = new Date().toISOString();
    const items = state.items.map((x) => x.read_at ? x : { ...x, read_at: now });
    set({ items, unread: 0 });
  }
}
