"use client";

import Link from "next/link";
import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { IconBell } from "./icons";
import { fmtRelative } from "./util";
import {
  markAllRead, markRead, snapshot, start, stop, subscribe,
  type Notification,
} from "../_lib/notifications";
import { useAuth } from "./auth-provider";

function useNotifications() {
  return useSyncExternalStore(subscribe, snapshot, snapshot);
}

const CATEGORY_LABEL: Record<Notification["category"], string> = {
  scan_commit: "Ops",
  security: "Security",
  user_lifecycle: "Users",
  pc_health: "PC health",
};

const SEVERITY_CLASS: Record<Notification["severity"], string> = {
  INFO: "pill-ok",
  WARN: "pill-warn",
  SEC: "pill-err",
};

export function NotificationBell() {
  const { status } = useAuth();
  const state = useNotifications();
  const [open, setOpen] = useState(false);
  const wrapRef = useRef<HTMLDivElement>(null);

  // Kick off the store once the user is authenticated; tear down on logout.
  useEffect(() => {
    if (status === "authenticated") start();
    return () => {
      if (status !== "authenticated") stop();
    };
  }, [status]);

  useEffect(() => {
    if (!open) return;
    const onClick = (e: MouseEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) setOpen(false);
    };
    window.addEventListener("mousedown", onClick);
    return () => window.removeEventListener("mousedown", onClick);
  }, [open]);

  const items = state.items.slice(0, 25);
  const unread = state.unread;

  return (
    <div ref={wrapRef} style={{ position: "relative" }}>
      <button
        className="icon-btn"
        aria-label="Notifications"
        onClick={() => setOpen((v) => !v)}
        style={{ position: "relative" }}
      >
        <IconBell />
        {unread > 0 && (
          <span
            aria-label={`${unread} unread`}
            style={{
              position: "absolute", top: 3, right: 3,
              minWidth: 16, height: 16, padding: "0 4px",
              background: "var(--err)", color: "white",
              fontSize: 10, fontWeight: 700, lineHeight: "16px",
              borderRadius: 8, textAlign: "center",
              boxShadow: "0 0 0 2px var(--surface)",
            }}
          >{unread > 99 ? "99+" : unread}</span>
        )}
      </button>

      {open && (
        <div style={{
          position: "absolute", top: "calc(100% + 8px)", right: 0,
          width: 380, maxHeight: 520, overflow: "hidden",
          background: "var(--surface)",
          border: "1px solid var(--border-strong)", borderRadius: 4,
          boxShadow: "0 14px 34px rgba(15,23,41,0.16)",
          zIndex: 60, display: "flex", flexDirection: "column",
        }}>
          <div style={{
            display: "flex", alignItems: "center", justifyContent: "space-between",
            padding: "12px 14px", borderBottom: "1px solid var(--border)",
          }}>
            <div style={{ fontSize: 12, fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.08em" }}>
              Notifications
              <span
                title={state.connected ? "Live" : "Reconnecting…"}
                style={{
                  display: "inline-block", width: 6, height: 6, borderRadius: "50%",
                  marginLeft: 8, verticalAlign: "middle",
                  background: state.connected ? "var(--ok)" : "var(--muted)",
                }}
              />
            </div>
            {unread > 0 && (
              <button
                className="btn"
                style={{ height: 26, padding: "0 10px", fontSize: 11.5 }}
                onClick={() => void markAllRead()}
              >Mark all read</button>
            )}
          </div>

          <div style={{ flex: 1, overflowY: "auto", minHeight: 0 }}>
            {items.length === 0 ? (
              <div className="mute" style={{ padding: "36px 20px", textAlign: "center", fontSize: 12.5 }}>
                No notifications yet.
              </div>
            ) : items.map((n) => (
              <NotificationRow key={n.id} n={n} onClose={() => setOpen(false)} />
            ))}
          </div>

          <Link
            href="/notifications"
            onClick={() => setOpen(false)}
            style={{
              display: "block", textAlign: "center", padding: "10px 14px",
              borderTop: "1px solid var(--border)",
              fontSize: 12, fontWeight: 500, color: "var(--accent)",
              textDecoration: "none", background: "var(--surface)",
            }}
          >View all notifications →</Link>
        </div>
      )}
    </div>
  );
}

function NotificationRow({ n, onClose }: { n: Notification; onClose: () => void }) {
  const unread = !n.read_at;
  const inner = (
    <>
      <div style={{ display: "flex", gap: 8, alignItems: "flex-start" }}>
        <span
          style={{
            marginTop: 6, flex: "0 0 auto",
            width: 7, height: 7, borderRadius: "50%",
            background: unread ? "var(--accent)" : "transparent",
          }}
        />
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 2 }}>
            <span className={`pill ${SEVERITY_CLASS[n.severity]}`} style={{ fontSize: 9 }}>
              {CATEGORY_LABEL[n.category] ?? n.category}
            </span>
            <span className="mono mute" style={{ fontSize: 10.5 }}>{fmtRelative(n.created_at)}</span>
          </div>
          <div style={{ fontSize: 13, fontWeight: unread ? 600 : 500, lineHeight: 1.35 }}>
            {n.title}
          </div>
          {n.body && (
            <div className="mute" style={{
              fontSize: 12, marginTop: 2, lineHeight: 1.4,
              whiteSpace: "pre-wrap", overflow: "hidden",
              display: "-webkit-box", WebkitLineClamp: 3, WebkitBoxOrient: "vertical",
            }}>{n.body}</div>
          )}
        </div>
      </div>
    </>
  );

  const rowStyle: React.CSSProperties = {
    display: "block",
    padding: "10px 14px",
    borderBottom: "1px solid var(--border)",
    background: unread ? "var(--accent-soft)" : "transparent",
    cursor: n.url ? "pointer" : "default",
    color: "inherit", textDecoration: "none",
  };

  const onClick = () => {
    if (unread) void markRead(n.id);
    onClose();
  };

  if (n.url) {
    return (
      <Link href={n.url} onClick={onClick} style={rowStyle}>
        {inner}
      </Link>
    );
  }
  return <div onClick={onClick} style={rowStyle}>{inner}</div>;
}
