"use client";

import { usePathname } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { useApp } from "./app-provider";
import { useAuth } from "./auth-provider";
import { CommandPalette } from "./command-palette";
import { NotificationBell } from "./notification-bell";
import { IconPlay, IconRefresh, IconSearch, IconUpload } from "./icons";

const TITLES: Record<string, string> = {
  "/":         "Dashboard",
  "/files":    "Files",
  "/pcs":      "PCs",
  "/logs":     "Logs",
  "/settings": "Settings",
};

function initialsFor(me: { first_name?: string | null; last_name?: string | null; email: string } | null): string {
  if (!me) return "?";
  const f = (me.first_name ?? "").trim(), l = (me.last_name ?? "").trim();
  if (f || l) return `${(f[0] ?? "").toUpperCase()}${(l[0] ?? "").toUpperCase()}` || me.email[0].toUpperCase();
  return me.email.slice(0, 2).toUpperCase();
}

export function Topbar() {
  const pathname = usePathname();
  const { running, run, setDrawerOpen, log } = useApp();
  const { me, logout } = useAuth();
  const title = TITLES[pathname] ?? "";
  const canTrigger = (me?.permissions ?? []).includes("run:trigger");
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);
  const isMac = typeof navigator !== "undefined" && /Mac/i.test(navigator.platform);

  useEffect(() => {
    if (!menuOpen) return;
    const onClick = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) setMenuOpen(false);
    };
    window.addEventListener("mousedown", onClick);
    return () => window.removeEventListener("mousedown", onClick);
  }, [menuOpen]);

  return (
    <header className="topbar">
      <div className="page-title">{title}</div>

      <div className="topbar-actions">
        <button className="btn" onClick={() => setPaletteOpen(true)} title="Search files & PCs (Ctrl+K)">
          <IconSearch /> Search
          <span className="mono mute" style={{ marginLeft: 6, fontSize: 10, border: "1px solid var(--border-strong)", borderRadius: 3, padding: "1px 5px" }}>
            {isMac ? "⌘K" : "Ctrl+K"}
          </span>
        </button>
        {log.length > 0 && (
          <button className="btn" onClick={() => setDrawerOpen(true)}>
            <IconRefresh /> Log
          </button>
        )}
        {canTrigger && (
          <>
            <button className="btn" onClick={() => run("scan")} disabled={!!running}>
              <IconPlay /> {running === "scan" ? "Scanning…" : "Scan"}
            </button>
            <button className="btn btn-primary" onClick={() => run("commit")} disabled={!!running}>
              <IconUpload /> {running === "commit" ? "Committing…" : "Commit"}
            </button>
          </>
        )}

        <div className="topbar-sep" />

        <NotificationBell />
        <div ref={menuRef} style={{ position: "relative" }}>
          <button
            className="avatar"
            onClick={() => setMenuOpen((v) => !v)}
            title={me?.email ?? ""}
            style={{ cursor: "pointer", border: "none" }}
          >
            {initialsFor(me)}
          </button>
          {menuOpen && me && (
            <div style={{
              position: "absolute", top: "calc(100% + 6px)", right: 0,
              minWidth: 220, background: "var(--surface)",
              border: "1px solid var(--border-strong)", borderRadius: 4,
              boxShadow: "0 12px 30px rgba(15,23,41,0.14)",
              zIndex: 40, overflow: "hidden",
            }}>
              <div style={{ padding: "12px 14px", borderBottom: "1px solid var(--border)" }}>
                <div style={{ fontSize: 13, fontWeight: 600, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                  {me.display_name || `${me.first_name ?? ""} ${me.last_name ?? ""}`.trim() || me.email}
                </div>
                <div className="mono mute" style={{ fontSize: 11, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                  {me.email}
                </div>
                <div style={{ marginTop: 6 }}>
                  <span className="pill pill-ok" style={{ textTransform: "uppercase", letterSpacing: "0.04em" }}>{me.role}</span>
                </div>
              </div>
              <button
                onClick={() => { setMenuOpen(false); void logout(); }}
                style={{
                  width: "100%", textAlign: "left",
                  padding: "10px 14px", background: "transparent", border: "none",
                  fontSize: 13, cursor: "pointer", color: "var(--ink)",
                }}
                onMouseEnter={(e) => e.currentTarget.style.background = "var(--bg)"}
                onMouseLeave={(e) => e.currentTarget.style.background = "transparent"}
              >
                Sign out
              </button>
            </div>
          )}
        </div>
      </div>
      <CommandPalette open={paletteOpen} onOpenChange={setPaletteOpen} />
    </header>
  );
}
