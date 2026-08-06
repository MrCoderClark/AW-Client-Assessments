"use client";

import * as Dialog from "@radix-ui/react-dialog";
import { useCallback, useEffect, useMemo, useState } from "react";
import { fmtRelative } from "../../_components/util";
import { IconLayout, IconRefresh } from "../../_components/icons";
import { useAuth } from "../../_components/auth-provider";
import { useConfirm } from "../../_components/confirm-dialog";
import { RequirePerm } from "../../_components/require-perm";
import {
  LAYOUT_OPTIONS, LayoutKey, ProfileRow,
  createProfile, deleteProfile, listProfiles, updateProfile,
} from "../../_lib/admin-profiles";

const LAYOUT_LABEL: Record<LayoutKey, string> = Object.fromEntries(
  LAYOUT_OPTIONS.map((o) => [o.value, o.label]),
) as Record<LayoutKey, string>;

export default function AdminProfilesPage() {
  return <RequirePerm perms={["user:read"]}><AdminProfilesPageInner /></RequirePerm>;
}

function AdminProfilesPageInner() {
  const { me } = useAuth();
  const canWrite = (me?.permissions ?? []).includes("user:write");

  const [rows, setRows] = useState<ProfileRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [editing, setEditing] = useState<ProfileRow | null>(null);
  const [creating, setCreating] = useState(false);

  const load = useCallback(async () => {
    setLoading(true); setErr(null);
    try {
      setRows(await listProfiles());
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => { load(); }, [load]);

  return (
    <>
      <div className="toolbar">
        <div className="spacer" />
        <button className="btn" onClick={load} disabled={loading}>
          <IconRefresh /> Refresh
        </button>
        {canWrite && (
          <button className="btn btn-primary" onClick={() => setCreating(true)}>
            <IconLayout /> New profile
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
              Profiles <span className="mute mono" style={{ fontWeight: 400, marginLeft: 8 }}>{rows.length}</span>
            </div>
            <div className="mute" style={{ fontSize: 12 }}>
              Assign a profile to a user in the Users page to change their dashboard.
            </div>
          </div>
          <table className="tbl">
            <thead>
              <tr>
                <th style={{ paddingLeft: 18 }}>Name</th>
                <th>Layout</th>
                <th>Description</th>
                <th style={{ width: 100 }}>Users</th>
                <th style={{ width: 100 }}>Created</th>
                <th style={{ width: 40 }}></th>
              </tr>
            </thead>
            <tbody>
              {loading && rows.length === 0 ? (
                <tr><td colSpan={6} style={{ padding: 24, color: "var(--muted)" }}>Loading…</td></tr>
              ) : rows.length === 0 ? (
                <tr><td colSpan={6} style={{ padding: 24, color: "var(--muted)" }}>No profiles.</td></tr>
              ) : rows.map((p) => (
                <tr key={p.id}
                    onClick={() => canWrite && setEditing(p)}
                    style={{ cursor: canWrite ? "pointer" : "default" }}>
                  <td style={{ paddingLeft: 18 }}>
                    <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                      <span style={{ fontWeight: 500 }}>{p.name}</span>
                      {p.is_system && <span className="pill" style={{ background: "var(--bg)", color: "var(--muted)", fontSize: 10 }}>System</span>}
                    </div>
                  </td>
                  <td className="mono mute" style={{ fontSize: 11 }}>
                    {LAYOUT_LABEL[p.layout_key] ?? p.layout_key}
                  </td>
                  <td className="mute" style={{ fontSize: 12 }}>{p.description || "—"}</td>
                  <td className="mono mute">{p.user_count}</td>
                  <td className="mono mute" style={{ fontSize: 11 }}>{fmtRelative(p.created_at)}</td>
                  <td style={{ textAlign: "right", paddingRight: 12 }}>
                    {canWrite && <span className="mute" style={{ fontSize: 16 }}>›</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {editing && (
        <ProfileDialog
          profile={editing}
          onClose={() => setEditing(null)}
          onSaved={() => { setEditing(null); load(); }}
        />
      )}
      {creating && (
        <ProfileDialog
          profile={null}
          onClose={() => setCreating(false)}
          onSaved={() => { setCreating(false); load(); }}
        />
      )}
    </>
  );
}

// ---------- create / edit dialog --------------------------------------

function ProfileDialog({ profile, onClose, onSaved }: {
  profile: ProfileRow | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const { confirm, dialog: confirmDialog } = useConfirm();
  const editing = profile !== null;
  const systemLocked = profile?.is_system ?? false;

  const [name, setName] = useState(profile?.name ?? "");
  const [description, setDescription] = useState(profile?.description ?? "");
  const [layoutKey, setLayoutKey] = useState<LayoutKey>(profile?.layout_key ?? "ops_default");
  const [busy, setBusy] = useState<"save" | "delete" | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const layoutHint = useMemo(
    () => LAYOUT_OPTIONS.find((o) => o.value === layoutKey)?.hint ?? "",
    [layoutKey],
  );

  const save = async () => {
    setBusy("save"); setErr(null);
    try {
      if (editing) {
        await updateProfile(profile!.id, {
          name: systemLocked ? undefined : name.trim(),
          description: description.trim() || null,
          layout_key: layoutKey,
        });
      } else {
        await createProfile({
          name: name.trim(),
          description: description.trim() || null,
          layout_key: layoutKey,
        });
      }
      onSaved();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  const doDelete = async () => {
    if (!profile) return;
    const ok = await confirm({
      title: `Delete profile "${profile.name}"?`,
      message: profile.user_count > 0
        ? `${profile.user_count} user(s) are assigned to this profile. You must reassign them before it can be deleted.`
        : "This cannot be undone.",
      confirmLabel: "Delete",
      danger: true,
    });
    if (ok === null) return;
    setBusy("delete"); setErr(null);
    try {
      await deleteProfile(profile.id);
      onSaved();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
      setBusy(null);
    }
  };

  return (
    <>
    {confirmDialog}
    <Dialog.Root open onOpenChange={(o) => { if (!o) onClose(); }}>
      <Dialog.Portal>
        <Dialog.Overlay className="drawer-overlay" />
        <Dialog.Content className="modal" style={{ maxWidth: 520, width: "90vw" }} aria-describedby={undefined}>
          <div className="modal-head">
            <Dialog.Title style={{ fontSize: 16, fontWeight: 600, letterSpacing: "-0.01em" }}>
              {editing ? `Edit "${profile!.name}"` : "New profile"}
            </Dialog.Title>
            <div className="mute" style={{ fontSize: 12, marginTop: 4 }}>
              {systemLocked
                ? "System profile — name is locked but description and layout can change."
                : "Name is shown to admins; users only see the effect (the assigned dashboard)."}
            </div>
          </div>

          <div style={{ padding: "18px 22px" }}>
            {err && (
              <div style={{ marginBottom: 12, padding: "8px 12px", background: "var(--err-soft)", color: "var(--err)", borderRadius: 3, fontSize: 12 }}>{err}</div>
            )}

            <Field label="Name">
              <input
                style={inputStyle}
                value={name}
                onChange={(e) => setName(e.target.value)}
                disabled={systemLocked}
                autoFocus={!systemLocked}
              />
            </Field>
            <Field label="Description">
              <textarea
                style={{ ...inputStyle, height: 60, padding: "8px 10px", resize: "vertical" }}
                value={description}
                onChange={(e) => setDescription(e.target.value)}
              />
            </Field>
            <Field label="Layout">
              <select
                value={layoutKey}
                onChange={(e) => setLayoutKey(e.target.value as LayoutKey)}
                style={inputStyle}
              >
                {LAYOUT_OPTIONS.map((o) => (
                  <option key={o.value} value={o.value}>{o.label}</option>
                ))}
              </select>
            </Field>
            <div className="mute" style={{ fontSize: 11, marginTop: -4, marginLeft: 140, marginBottom: 12 }}>
              {layoutHint}
            </div>
          </div>

          <div className="modal-foot" style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
            <div>
              {editing && !systemLocked && (
                <button
                  className="btn"
                  onClick={doDelete}
                  disabled={busy !== null}
                  style={{ color: "var(--err)", borderColor: "var(--err-soft)" }}
                >
                  {busy === "delete" ? "Deleting…" : "Delete"}
                </button>
              )}
            </div>
            <div style={{ display: "flex", gap: 8 }}>
              <Dialog.Close asChild><button className="btn">Cancel</button></Dialog.Close>
              <button
                className="btn btn-primary"
                onClick={save}
                disabled={busy !== null || (!systemLocked && !name.trim())}
              >{busy === "save" ? "Saving…" : editing ? "Save" : "Create"}</button>
            </div>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
    </>
  );
}

// ---------- shared bits -----------------------------------------------

const inputStyle: React.CSSProperties = {
  width: "100%", height: 32, padding: "0 10px", fontSize: 13,
  background: "var(--surface)", border: "1px solid var(--border-strong)",
  borderRadius: 3, color: "var(--ink)", fontFamily: "var(--font-sans)",
};

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div style={{ display: "grid", gridTemplateColumns: "130px 1fr", gap: 10, alignItems: "center", marginBottom: 10, fontSize: 13 }}>
      <div className="mute" style={{ fontSize: 11, textTransform: "uppercase", letterSpacing: "0.06em", fontWeight: 500 }}>{label}</div>
      <div>{children}</div>
    </div>
  );
}
