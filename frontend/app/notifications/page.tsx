"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { fmtRelative } from "../_components/util";
import { IconRefresh, IconSearch } from "../_components/icons";
import {
  listHistory, markAllRead, markRead,
  type ListResponse, type Notification,
} from "../_lib/notifications";

type CatFilter = "" | Notification["category"];
type SevFilter = "" | Notification["severity"];

const CATEGORY_LABEL: Record<Notification["category"], string> = {
  scan_commit: "Ops",
  security: "Security",
  user_lifecycle: "Users",
  pc_health: "PC health",
};

const SEV_CLASS: Record<Notification["severity"], string> = {
  INFO: "pill-ok", WARN: "pill-warn", SEC: "pill-err",
};

const PAGE_SIZE = 50;

const CAT_CHIPS: { value: CatFilter; label: string }[] = [
  { value: "",              label: "All" },
  { value: "scan_commit",   label: "Ops" },
  { value: "security",      label: "Security" },
  { value: "user_lifecycle",label: "Users" },
  { value: "pc_health",     label: "PC health" },
];

export default function NotificationsPage() {
  const [data, setData] = useState<ListResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [cat, setCat] = useState<CatFilter>("");
  const [sev, setSev] = useState<SevFilter>("");
  const [unreadOnly, setUnreadOnly] = useState(false);
  const [q, setQ] = useState("");
  const [page, setPage] = useState(0);

  const load = useCallback(async () => {
    setLoading(true); setErr(null);
    try {
      const j = await listHistory({
        limit: PAGE_SIZE,
        offset: page * PAGE_SIZE,
        category: cat || undefined,
        severity: sev || undefined,
        unreadOnly,
      });
      setData(j);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [cat, sev, unreadOnly, page]);

  useEffect(() => { load(); }, [load]);

  // Filter chip / severity / unread changes reset to page 0.
  useEffect(() => { setPage(0); }, [cat, sev, unreadOnly]);

  const filtered = useMemo(() => {
    if (!data) return [];
    const term = q.trim().toLowerCase();
    if (!term) return data.notifications;
    return data.notifications.filter((n) =>
      n.title.toLowerCase().includes(term)
      || n.body.toLowerCase().includes(term)
      || n.kind.toLowerCase().includes(term),
    );
  }, [data, q]);

  const total = data?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  const doMarkOne = async (id: string) => {
    await markRead(id);
    load();
  };
  const doMarkAll = async () => {
    await markAllRead();
    load();
  };

  return (
    <>
      <div className="toolbar">
        <div className="search">
          <span className="search-icon"><IconSearch /></span>
          <input
            placeholder="Filter loaded page…"
            value={q}
            onChange={(e) => setQ(e.target.value)}
          />
        </div>
        <div className="chips">
          {CAT_CHIPS.map((c) => (
            <button
              key={c.value}
              className={`chip${cat === c.value ? " active" : ""}`}
              onClick={() => setCat(c.value)}
            >{c.label}</button>
          ))}
        </div>
        <select
          className="chip"
          style={{ borderRadius: 3, border: "1px solid var(--border-strong)", height: 32 }}
          value={sev}
          onChange={(e) => setSev(e.target.value as SevFilter)}
        >
          <option value="">All severities</option>
          <option value="INFO">Info</option>
          <option value="WARN">Warn</option>
          <option value="SEC">Security</option>
        </select>
        <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12, color: "var(--muted)" }}>
          <input type="checkbox" checked={unreadOnly} onChange={(e) => setUnreadOnly(e.target.checked)} />
          Unread only
        </label>
        <div className="spacer" />
        <button className="btn" onClick={load} disabled={loading}>
          <IconRefresh /> Refresh
        </button>
        {data && data.unread > 0 && (
          <button className="btn btn-primary" onClick={doMarkAll}>
            Mark all read
          </button>
        )}
      </div>

      <div style={{ padding: 28 }}>
        {err && (
          <div style={{ marginBottom: 12, padding: "10px 14px", background: "var(--err-soft)", color: "var(--err)", borderRadius: 3, fontSize: 12 }}>
            {err}
          </div>
        )}

        <div className="card">
          <div className="card-head">
            <div className="card-title">
              Notifications
              <span className="mute mono" style={{ fontWeight: 400, marginLeft: 8 }}>
                {total}{data?.unread ? ` · ${data.unread} unread` : ""}
              </span>
            </div>
          </div>

          {loading && !data ? (
            <div style={{ padding: 30, color: "var(--muted)" }}>Loading…</div>
          ) : filtered.length === 0 ? (
            <div style={{ padding: 30, color: "var(--muted)" }}>
              {q ? "Nothing matches your filter on this page." : "No notifications."}
            </div>
          ) : (
            <table className="tbl">
              <thead>
                <tr>
                  <th style={{ width: 22, paddingLeft: 18 }}></th>
                  <th style={{ width: 110 }}>Category</th>
                  <th>Title</th>
                  <th style={{ width: 130 }}>When</th>
                  <th style={{ width: 90 }}></th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((n) => {
                  const unread = !n.read_at;
                  return (
                    <tr key={n.id}>
                      <td style={{ paddingLeft: 18 }}>
                        <span style={{
                          display: "inline-block", width: 7, height: 7, borderRadius: "50%",
                          background: unread ? "var(--accent)" : "transparent",
                        }} />
                      </td>
                      <td>
                        <span className={`pill ${SEV_CLASS[n.severity]}`} style={{ fontSize: 10 }}>
                          {CATEGORY_LABEL[n.category] ?? n.category}
                        </span>
                      </td>
                      <td>
                        <div style={{ fontSize: 13, fontWeight: unread ? 600 : 500 }}>
                          {n.url ? (
                            <Link href={n.url} style={{ color: "inherit", textDecoration: "none" }}>
                              {n.title}
                            </Link>
                          ) : n.title}
                        </div>
                        {n.body && (
                          <div className="mute" style={{
                            fontSize: 12, marginTop: 2, lineHeight: 1.35,
                            whiteSpace: "pre-wrap",
                            overflow: "hidden",
                            display: "-webkit-box",
                            WebkitLineClamp: 2,
                            WebkitBoxOrient: "vertical",
                          }}>{n.body}</div>
                        )}
                      </td>
                      <td className="mono mute" style={{ fontSize: 11 }}>
                        {fmtRelative(n.created_at)}
                      </td>
                      <td>
                        {unread && (
                          <button
                            className="btn"
                            style={{ height: 26, padding: "0 10px", fontSize: 11.5 }}
                            onClick={() => doMarkOne(n.id)}
                          >Mark read</button>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}

          {totalPages > 1 && (
            <div style={{
              display: "flex", justifyContent: "space-between", alignItems: "center",
              padding: "12px 18px", borderTop: "1px solid var(--border)",
              fontSize: 12, color: "var(--muted)",
            }}>
              <div>Page {page + 1} of {totalPages}</div>
              <div style={{ display: "flex", gap: 6 }}>
                <button className="btn" disabled={page === 0} onClick={() => setPage((p) => Math.max(0, p - 1))}>Prev</button>
                <button className="btn" disabled={page >= totalPages - 1} onClick={() => setPage((p) => p + 1)}>Next</button>
              </div>
            </div>
          )}
        </div>
      </div>
    </>
  );
}
