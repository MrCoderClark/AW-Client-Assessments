"use client";

// Customize dashboard drawer. Lets a user pick which widgets appear on
// their dashboard and in what order (via up/down arrows). Save writes to
// users.dashboard_widgets. "Reset to profile default" clears the override
// (null) so the user follows the profile again.

import * as Dialog from "@radix-ui/react-dialog";
import { useEffect, useMemo, useState } from "react";
import { useAuth } from "../auth-provider";
import { saveDashboardWidgets } from "../../_lib/auth";
import { WIDGETS, widgetsForProfile } from "./widgets";

type Row = { key: string; enabled: boolean };

export function CustomizeDrawer({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { me, reloadMe } = useAuth();
  const [rows, setRows] = useState<Row[]>([]);
  const [busy, setBusy] = useState<"save" | "reset" | null>(null);
  const [err, setErr] = useState<string | null>(null);

  // Load: enabled = user's current widgets (in order), then remaining
  // catalog entries appended as disabled.
  useEffect(() => {
    if (!open || !me) return;
    const current = me.dashboard_widgets ?? widgetsForProfile(me.profile?.layout_key);
    const inList = new Set(current);
    const remaining = Object.keys(WIDGETS).filter((k) => !inList.has(k));
    setRows([
      ...current.filter((k) => WIDGETS[k]).map((k) => ({ key: k, enabled: true })),
      ...remaining.map((k) => ({ key: k, enabled: false })),
    ]);
    setErr(null);
  }, [open, me]);

  const enabledKeys = useMemo(() => rows.filter((r) => r.enabled).map((r) => r.key), [rows]);

  const toggle = (key: string) => setRows((prev) => prev.map((r) => r.key === key ? { ...r, enabled: !r.enabled } : r));

  const move = (idx: number, dir: -1 | 1) => setRows((prev) => {
    const next = [...prev];
    const target = idx + dir;
    if (target < 0 || target >= next.length) return next;
    [next[idx], next[target]] = [next[target], next[idx]];
    return next;
  });

  const doSave = async () => {
    setBusy("save"); setErr(null);
    try {
      await saveDashboardWidgets(enabledKeys);
      await reloadMe();
      onClose();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally { setBusy(null); }
  };

  const doReset = async () => {
    setBusy("reset"); setErr(null);
    try {
      await saveDashboardWidgets(null);
      await reloadMe();
      onClose();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally { setBusy(null); }
  };

  const usingCustom = me?.dashboard_widgets !== null && me?.dashboard_widgets !== undefined;

  return (
    <Dialog.Root open={open} onOpenChange={(o) => { if (!o) onClose(); }}>
      <Dialog.Portal>
        <Dialog.Overlay className="drawer-overlay" />
        <Dialog.Content className="drawer" aria-describedby={undefined}>
          <div className="drawer-head">
            <Dialog.Title className="drawer-title">Customize dashboard</Dialog.Title>
            <Dialog.Close asChild><button className="btn">Close</button></Dialog.Close>
          </div>

          <div style={{ padding: "16px 20px", overflow: "auto", flex: 1 }}>
            <div className="mute" style={{ fontSize: 12, marginBottom: 16 }}>
              Check widgets to add them. Uncheck to remove.
              Use ▲▼ to reorder. Stat tiles always show above cards.
              {usingCustom && <span style={{ color: "var(--accent)", marginLeft: 8 }}>Custom layout active.</span>}
            </div>

            {err && (
              <div style={{ marginBottom: 12, padding: "8px 12px", background: "var(--err-soft)", color: "var(--err)", borderRadius: 3, fontSize: 12 }}>{err}</div>
            )}

            <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
              {rows.map((r, i) => {
                const def = WIDGETS[r.key];
                if (!def) return null;
                return (
                  <div
                    key={r.key}
                    style={{
                      display: "grid", gridTemplateColumns: "auto 40px 1fr auto", alignItems: "center", gap: 12,
                      padding: "10px 12px",
                      background: r.enabled ? "var(--surface)" : "var(--bg)",
                      border: "1px solid var(--border)", borderRadius: 3,
                    }}
                  >
                    <input
                      type="checkbox"
                      checked={r.enabled}
                      onChange={() => toggle(r.key)}
                      style={{ width: 16, height: 16 }}
                    />
                    <span className="pill" style={{
                      background: def.kind === "stat" ? "var(--accent-soft)" : "var(--bg)",
                      color: def.kind === "stat" ? "var(--accent)" : "var(--muted)",
                      fontSize: 10, textTransform: "uppercase",
                    }}>{def.kind}</span>
                    <div>
                      <div style={{ fontSize: 13, fontWeight: 500 }}>{def.label}</div>
                      <div className="mute" style={{ fontSize: 11 }}>{def.description}</div>
                    </div>
                    <div style={{ display: "flex", gap: 4 }}>
                      <button
                        className="btn"
                        onClick={() => move(i, -1)}
                        disabled={i === 0}
                        style={{ padding: "2px 6px", minWidth: 24, fontSize: 11 }}
                        title="Move up"
                      >▲</button>
                      <button
                        className="btn"
                        onClick={() => move(i, 1)}
                        disabled={i === rows.length - 1}
                        style={{ padding: "2px 6px", minWidth: 24, fontSize: 11 }}
                        title="Move down"
                      >▼</button>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>

          <div style={{
            padding: "12px 20px", borderTop: "1px solid var(--border)",
            display: "flex", justifyContent: "space-between", gap: 8,
          }}>
            <button
              className="btn"
              onClick={doReset}
              disabled={busy !== null || !usingCustom}
              title={usingCustom ? "Revert to your profile's default layout" : "Already following profile default"}
            >
              {busy === "reset" ? "Resetting…" : "Reset to profile default"}
            </button>
            <div style={{ display: "flex", gap: 8 }}>
              <Dialog.Close asChild><button className="btn">Cancel</button></Dialog.Close>
              <button
                className="btn btn-primary"
                onClick={doSave}
                disabled={busy !== null}
              >{busy === "save" ? "Saving…" : "Save"}</button>
            </div>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
