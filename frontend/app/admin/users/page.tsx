"use client";

import * as Dialog from "@radix-ui/react-dialog";
import { useCallback, useEffect, useMemo, useState } from "react";
import { fmtRelative } from "../../_components/util";
import { IconRefresh, IconSearch, IconUsers } from "../../_components/icons";
import { useAuth } from "../../_components/auth-provider";
import { useConfirm } from "../../_components/confirm-dialog";
import { RequirePerm } from "../../_components/require-perm";
import {
  UserRow, listUsers, updateUser, suspendUser, reactivateUser,
  forceResetUser, deleteUser, inviteUser, getUser,
} from "../../_lib/admin-users";
import { ProfileRow, listProfiles } from "../../_lib/admin-profiles";

type StatusFilter = "" | UserRow["status"];
type RoleFilter = "" | UserRow["role"];

const STATUS_CHIPS: { value: StatusFilter; label: string }[] = [
  { value: "",          label: "All" },
  { value: "ACTIVE",    label: "Active" },
  { value: "INVITED",   label: "Invited" },
  { value: "SUSPENDED", label: "Suspended" },
];

const ROLE_LABELS: Record<UserRow["role"], string> = {
  admin: "Admin", operator: "Operator", viewer: "Viewer",
};

function statusPill(s: UserRow["status"]): { cls: string; label: string } {
  switch (s) {
    case "ACTIVE":       return { cls: "pill-ok",   label: "Active" };
    case "INVITED":      return { cls: "pill-warn", label: "Invited" };
    case "SUSPENDED":    return { cls: "pill-err",  label: "Suspended" };
    case "DEACTIVATED":  return { cls: "pill-warn", label: "Deactivated" };
    case "SOFT_DELETED": return { cls: "pill-err",  label: "Deleted" };
  }
}

function initials(u: UserRow): string {
  const f = (u.first_name || "").trim(); const l = (u.last_name || "").trim();
  if (f || l) return `${f[0] || ""}${l[0] || ""}`.toUpperCase();
  return u.email.slice(0, 2).toUpperCase();
}

function fullName(u: UserRow): string {
  return u.display_name?.trim()
    || `${u.first_name || ""} ${u.last_name || ""}`.trim()
    || u.email;
}

export default function AdminUsersPage() {
  return <RequirePerm perms={["user:read"]}><AdminUsersPageInner /></RequirePerm>;
}

function AdminUsersPageInner() {
  const { me } = useAuth();
  const canWrite = (me?.permissions || []).includes("user:write");
  const canInvite = (me?.permissions || []).includes("user:invite");

  const [rows, setRows] = useState<UserRow[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [q, setQ] = useState("");
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("");
  const [roleFilter, setRoleFilter] = useState<RoleFilter>("");
  const [includeDeleted, setIncludeDeleted] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [inviteOpen, setInviteOpen] = useState(false);

  const load = useCallback(async () => {
    setLoading(true); setErr(null);
    try {
      const r = await listUsers({
        q: q.trim() || undefined,
        status: statusFilter || undefined,
        role: roleFilter || undefined,
        includeDeleted,
        limit: 100,
      });
      setRows(r.users); setTotal(r.total);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [q, statusFilter, roleFilter, includeDeleted]);

  useEffect(() => { load(); }, [load]);

  const selected = useMemo(
    () => rows.find(r => r.id === selectedId) ?? null,
    [rows, selectedId],
  );

  return (
    <>
      <div className="toolbar">
        <div className="search">
          <span className="search-icon"><IconSearch /></span>
          <input
            placeholder="Search email or name…"
            value={q}
            onChange={(e) => setQ(e.target.value)}
          />
        </div>
        <div className="chips">
          {STATUS_CHIPS.map((c) => (
            <button
              key={c.value}
              className={`chip${statusFilter === c.value ? " active" : ""}`}
              onClick={() => setStatusFilter(c.value)}
            >{c.label}</button>
          ))}
        </div>
        <select
          className="chip"
          style={{ borderRadius: 3, border: "1px solid var(--border-strong)", height: 32 }}
          value={roleFilter}
          onChange={(e) => setRoleFilter(e.target.value as RoleFilter)}
        >
          <option value="">All roles</option>
          <option value="admin">Admin</option>
          <option value="operator">Operator</option>
          <option value="viewer">Viewer</option>
        </select>
        <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12, color: "var(--muted)" }}>
          <input type="checkbox" checked={includeDeleted} onChange={(e) => setIncludeDeleted(e.target.checked)} />
          Show deleted
        </label>
        <div className="spacer" />
        <button className="btn" onClick={load} disabled={loading}>
          <IconRefresh /> Refresh
        </button>
        {canInvite && (
          <button className="btn btn-primary" onClick={() => setInviteOpen(true)}>
            <IconUsers /> Invite user
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
              Users <span className="mute mono" style={{ fontWeight: 400, marginLeft: 8 }}>{total}</span>
            </div>
          </div>
          <table className="tbl">
            <thead>
              <tr>
                <th style={{ paddingLeft: 18 }}>User</th>
                <th>Role</th>
                <th>Status</th>
                <th>MFA</th>
                <th>Last login</th>
                <th>Invited / created</th>
              </tr>
            </thead>
            <tbody>
              {loading && rows.length === 0 ? (
                <tr><td colSpan={6} style={{ padding: 24, color: "var(--muted)" }}>Loading…</td></tr>
              ) : rows.length === 0 ? (
                <tr><td colSpan={6} style={{ padding: 24, color: "var(--muted)" }}>No users match.</td></tr>
              ) : rows.map((u) => {
                const p = statusPill(u.status);
                return (
                  <tr key={u.id} onClick={() => setSelectedId(u.id)} style={{ cursor: "pointer" }}>
                    <td style={{ paddingLeft: 18 }}>
                      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                        <div style={{
                          width: 30, height: 30, borderRadius: "50%",
                          background: "var(--accent-soft)", color: "var(--accent)",
                          display: "grid", placeItems: "center",
                          fontFamily: "var(--font-mono)", fontSize: 11, fontWeight: 600,
                        }}>{initials(u)}</div>
                        <div>
                          <div style={{ fontWeight: 500 }}>{fullName(u)}</div>
                          <div className="mono mute" style={{ fontSize: 11 }}>{u.email}</div>
                        </div>
                      </div>
                    </td>
                    <td>
                      <span className="pill" style={{ background: "var(--bg)", color: "var(--ink)" }}>
                        {ROLE_LABELS[u.role]}
                      </span>
                    </td>
                    <td><span className={`pill ${p.cls}`}>{p.label}</span></td>
                    <td className="mono mute" style={{ fontSize: 11 }}>{u.mfa_enrolled ? "Yes" : "—"}</td>
                    <td className="mono mute" style={{ fontSize: 11 }}>
                      {u.last_login_at ? fmtRelative(u.last_login_at) : "Never"}
                    </td>
                    <td className="mono mute" style={{ fontSize: 11 }}>
                      {fmtRelative(u.created_at)}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>

      <UserDrawer
        userId={selectedId}
        canWrite={canWrite}
        onClose={() => setSelectedId(null)}
        onChanged={load}
      />

      {canInvite && (
        <InviteModal
          open={inviteOpen}
          onClose={() => setInviteOpen(false)}
          onInvited={load}
        />
      )}
    </>
  );
}

// ---------- Drawer -----------------------------------------------------

function UserDrawer({ userId, canWrite, onClose, onChanged }: {
  userId: string | null;
  canWrite: boolean;
  onClose: () => void;
  onChanged: () => void;
}) {
  const { confirm, dialog: confirmDialog } = useConfirm();
  const [u, setU] = useState<UserRow | null>(null);
  const [profiles, setProfiles] = useState<ProfileRow[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [form, setForm] = useState({ first_name: "", last_name: "", display_name: "", email: "", role: "viewer" as UserRow["role"], profile_id: "" });

  // Load the profile list once when the drawer is opened. Cheap: 3-N rows.
  useEffect(() => {
    if (!userId || profiles.length) return;
    listProfiles().then(setProfiles).catch(() => { /* profile picker will just be empty */ });
  }, [userId, profiles.length]);

  useEffect(() => {
    if (!userId) return;
    let alive = true;
    setErr(null); setMsg(null); setEditing(false);
    getUser(userId).then((row) => {
      if (!alive) return;
      setU(row);
      setForm({
        first_name: row.first_name || "",
        last_name: row.last_name || "",
        display_name: row.display_name || "",
        email: row.email,
        role: row.role,
        profile_id: row.profile?.id ?? "",
      });
    }).catch((e) => alive && setErr(String(e?.message ?? e)));
    return () => { alive = false; };
  }, [userId]);

  const wrap = async (action: string, fn: () => Promise<UserRow | void | { reset_url: string; mail_ok: boolean }>) => {
    setBusy(action); setErr(null); setMsg(null);
    try {
      const out = await fn();
      if (out && "reset_url" in out) {
        setMsg(out.mail_ok
          ? "Reset link mailed to the user."
          : `Mail failed — copy this link: ${out.reset_url}`);
      }
      onChanged();
      if (userId) {
        const fresh = await getUser(userId);
        setU(fresh);
      }
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  const doSave = () => wrap("save", () => updateUser(u!.id, {
    first_name: form.first_name,
    last_name: form.last_name,
    display_name: form.display_name,
    email: form.email,
    role: form.role,
    profile_id: form.profile_id,
  }).then((r) => { setEditing(false); return r; }));

  const doSuspend = async () => {
    const reason = await confirm({
      title: `Suspend ${u!.email}?`,
      message: "The user's sessions will be revoked immediately. They will not be able to log in until reactivated.",
      confirmLabel: "Suspend",
      danger: true,
      input: {
        label: "Reason (shown in audit log)",
        placeholder: "Optional",
        initial: "",
      },
    });
    if (reason === null) return;
    wrap("suspend", () => suspendUser(u!.id, reason));
  };
  const doReactivate = () => wrap("reactivate", () => reactivateUser(u!.id));
  const doForceReset = async () => {
    const ok = await confirm({
      title: "Force password reset?",
      message: "Sessions will be revoked and a reset link mailed to the user.",
      confirmLabel: "Force reset",
      danger: true,
    });
    if (ok === null) return;
    wrap("force-reset", () => forceResetUser(u!.id));
  };
  const doDelete = async (hard: boolean) => {
    const ok = await confirm({
      title: hard ? "Permanently delete user?" : "Soft-delete user?",
      message: hard
        ? `This removes ${u!.email} from the database. This cannot be undone.`
        : `${u!.email} will be marked deleted. Their sessions will be revoked and they will no longer appear in the default list.`,
      confirmLabel: hard ? "Permanently delete" : "Soft-delete",
      danger: true,
    });
    if (ok === null) return;
    wrap(hard ? "hard-delete" : "delete", async () => {
      await deleteUser(u!.id, { hard });
      onChanged();
      onClose();
    });
  };

  const open = userId !== null;
  return (
    <>
    {confirmDialog}
    <Dialog.Root open={open} onOpenChange={(o) => { if (!o) onClose(); }}>
      <Dialog.Portal>
        <Dialog.Overlay className="drawer-overlay" />
        <Dialog.Content className="drawer" aria-describedby={undefined}>
          <div className="drawer-head">
            <Dialog.Title className="drawer-title">
              <IconUsers /> User details
            </Dialog.Title>
            <Dialog.Close asChild><button className="btn">Close</button></Dialog.Close>
          </div>

          {!u ? (
            <div style={{ padding: 20, color: "var(--muted)" }}>
              {err ? <span style={{ color: "var(--err)" }}>{err}</span> : "Loading…"}
            </div>
          ) : (
            <div style={{ padding: "16px 20px", overflow: "auto" }}>
              <div style={{ display: "flex", alignItems: "center", gap: 14, marginBottom: 18 }}>
                <div style={{
                  width: 52, height: 52, borderRadius: "50%",
                  background: "var(--accent-soft)", color: "var(--accent)",
                  display: "grid", placeItems: "center",
                  fontFamily: "var(--font-mono)", fontSize: 16, fontWeight: 600,
                }}>{initials(u)}</div>
                <div style={{ flex: 1 }}>
                  <div style={{ fontSize: 15, fontWeight: 600 }}>{fullName(u)}</div>
                  <div className="mono mute" style={{ fontSize: 11 }}>{u.email}</div>
                  <div style={{ display: "flex", gap: 6, marginTop: 6 }}>
                    <span className={`pill ${statusPill(u.status).cls}`}>{statusPill(u.status).label}</span>
                    <span className="pill" style={{ background: "var(--bg)", color: "var(--ink)" }}>{ROLE_LABELS[u.role]}</span>
                    {u.mfa_enrolled && <span className="pill pill-ok">MFA</span>}
                    {u.must_change_password && <span className="pill pill-warn">Force-reset pending</span>}
                    {u.locked_until && <span className="pill pill-err">Locked</span>}
                  </div>
                </div>
              </div>

              {err && (
                <div style={{ marginBottom: 12, padding: "8px 12px", background: "var(--err-soft)", color: "var(--err)", borderRadius: 3, fontSize: 12 }}>{err}</div>
              )}
              {msg && (
                <div style={{ marginBottom: 12, padding: "8px 12px", background: "var(--ok-soft)", color: "var(--ok)", borderRadius: 3, fontSize: 12 }}>{msg}</div>
              )}

              {/* Profile block */}
              <section style={{ marginBottom: 20 }}>
                <SectionHead
                  label="Profile"
                  right={canWrite ? (
                    editing
                      ? (
                        <>
                          <button className="btn" disabled={busy !== null} onClick={() => setEditing(false)}>Cancel</button>
                          <button className="btn btn-primary" disabled={busy !== null} onClick={doSave}>
                            {busy === "save" ? "Saving…" : "Save"}
                          </button>
                        </>
                      )
                      : <button className="btn" onClick={() => setEditing(true)}>Edit</button>
                  ) : null}
                />
                <Field label="First name">
                  {editing
                    ? <TextInput value={form.first_name} onChange={(v) => setForm({ ...form, first_name: v })} />
                    : u.first_name || <span className="mute">—</span>}
                </Field>
                <Field label="Last name">
                  {editing
                    ? <TextInput value={form.last_name} onChange={(v) => setForm({ ...form, last_name: v })} />
                    : u.last_name || <span className="mute">—</span>}
                </Field>
                <Field label="Display name">
                  {editing
                    ? <TextInput value={form.display_name} onChange={(v) => setForm({ ...form, display_name: v })} />
                    : u.display_name || <span className="mute">—</span>}
                </Field>
                <Field label="Email">
                  {editing
                    ? <TextInput value={form.email} onChange={(v) => setForm({ ...form, email: v })} />
                    : u.email}
                </Field>
                <Field label="Role">
                  {editing ? (
                    <select
                      value={form.role}
                      onChange={(e) => setForm({ ...form, role: e.target.value as UserRow["role"] })}
                      style={inputStyle}
                    >
                      <option value="admin">Admin</option>
                      <option value="operator">Operator</option>
                      <option value="viewer">Viewer</option>
                    </select>
                  ) : ROLE_LABELS[u.role]}
                </Field>
                <Field label="Profile">
                  {editing ? (
                    <select
                      value={form.profile_id}
                      onChange={(e) => setForm({ ...form, profile_id: e.target.value })}
                      style={inputStyle}
                    >
                      <option value="">— None (default dashboard) —</option>
                      {profiles.map((p) => (
                        <option key={p.id} value={p.id}>
                          {p.name}{p.is_system ? " (system)" : ""}
                        </option>
                      ))}
                    </select>
                  ) : u.profile ? u.profile.name : <span className="mute">None</span>}
                </Field>
              </section>

              {/* Activity */}
              <section style={{ marginBottom: 20 }}>
                <SectionHead label="Activity" />
                <Field label="Last login">
                  {u.last_login_at ? `${fmtRelative(u.last_login_at)} · ${u.last_login_ip || "—"}` : <span className="mute">Never</span>}
                </Field>
                <Field label="Failed attempts">{u.failed_login_attempts}</Field>
                <Field label="Locked until">
                  {u.locked_until ? fmtRelative(u.locked_until) : <span className="mute">—</span>}
                </Field>
                <Field label="Email verified">{u.email_verified ? "Yes" : <span className="mute">No</span>}</Field>
                <Field label="Created">{fmtRelative(u.created_at)}</Field>
                {u.suspended_at && <Field label="Suspended">{fmtRelative(u.suspended_at)} — {u.suspended_reason || "no reason given"}</Field>}
                {u.deleted_at && <Field label="Deleted">{fmtRelative(u.deleted_at)}</Field>}
              </section>

              {/* Actions */}
              {canWrite && (
                <section>
                  <SectionHead label="Actions" />
                  <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                    {u.status === "SUSPENDED" ? (
                      <button className="btn" disabled={busy !== null} onClick={doReactivate}>
                        {busy === "reactivate" ? "Reactivating…" : "Reactivate"}
                      </button>
                    ) : u.status === "ACTIVE" || u.status === "INVITED" ? (
                      <button className="btn" disabled={busy !== null} onClick={doSuspend}>
                        {busy === "suspend" ? "Suspending…" : "Suspend account"}
                      </button>
                    ) : null}
                    {u.deleted_at === null && (
                      <button className="btn" disabled={busy !== null} onClick={doForceReset}>
                        {busy === "force-reset" ? "Sending link…" : "Force password reset"}
                      </button>
                    )}
                    {u.deleted_at === null ? (
                      <button className="btn" disabled={busy !== null} onClick={() => doDelete(false)}
                        style={{ color: "var(--err)", borderColor: "var(--err-soft)" }}>
                        {busy === "delete" ? "Deleting…" : "Soft-delete"}
                      </button>
                    ) : (
                      <button className="btn" disabled={busy !== null} onClick={() => doDelete(true)}
                        style={{ color: "var(--err)", borderColor: "var(--err-soft)" }}>
                        {busy === "hard-delete" ? "Deleting…" : "Permanently delete"}
                      </button>
                    )}
                  </div>
                </section>
              )}
            </div>
          )}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
    </>
  );
}

// ---------- Invite modal ----------------------------------------------

function InviteModal({ open, onClose, onInvited }: {
  open: boolean; onClose: () => void; onInvited: () => void;
}) {
  const [email, setEmail] = useState("");
  const [first, setFirst] = useState("");
  const [last, setLast] = useState("");
  const [role, setRole] = useState<UserRow["role"]>("viewer");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [ok, setOk] = useState<{ url: string; mailed: boolean } | null>(null);

  useEffect(() => {
    if (open) {
      setEmail(""); setFirst(""); setLast(""); setRole("viewer");
      setErr(null); setOk(null); setBusy(false);
    }
  }, [open]);

  const submit = async () => {
    setBusy(true); setErr(null);
    try {
      const r = await inviteUser({
        email: email.trim(), role, first_name: first.trim(), last_name: last.trim(),
      });
      setOk({ url: r.invite_url, mailed: r.mail_ok });
      onInvited();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog.Root open={open} onOpenChange={(o) => { if (!o) onClose(); }}>
      <Dialog.Portal>
        <Dialog.Overlay className="drawer-overlay" />
        <Dialog.Content className="modal" style={{ maxWidth: 480, width: "90vw" }} aria-describedby={undefined}>
          <div className="modal-head">
            <Dialog.Title style={{ fontSize: 16, fontWeight: 600, letterSpacing: "-0.01em" }}>
              Invite a user
            </Dialog.Title>
            <div className="mute" style={{ fontSize: 12, marginTop: 4 }}>
              They'll get an email with a link to set their password.
            </div>
          </div>

          <div style={{ padding: "18px 22px" }}>
            {ok ? (
              <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
                <div style={{ padding: "10px 14px", background: "var(--ok-soft)", color: "var(--ok)", borderRadius: 3, fontSize: 12 }}>
                  {ok.mailed ? "Invite email sent." : "User created, but the invite email did NOT send. Copy this link:"}
                </div>
                <div className="mono" style={{
                  padding: "10px 12px", background: "var(--bg)", border: "1px solid var(--border)",
                  borderRadius: 3, fontSize: 11, wordBreak: "break-all",
                }}>{ok.url}</div>
                <button className="btn" onClick={() => navigator.clipboard.writeText(ok.url)}>Copy link</button>
              </div>
            ) : (
              <>
                {err && (
                  <div style={{ marginBottom: 12, padding: "8px 12px", background: "var(--err-soft)", color: "var(--err)", borderRadius: 3, fontSize: 12 }}>{err}</div>
                )}
                <Field label="Email"><TextInput value={email} onChange={setEmail} autoFocus /></Field>
                <Field label="First name"><TextInput value={first} onChange={setFirst} /></Field>
                <Field label="Last name"><TextInput value={last} onChange={setLast} /></Field>
                <Field label="Role">
                  <select value={role} onChange={(e) => setRole(e.target.value as UserRow["role"])} style={inputStyle}>
                    <option value="viewer">Viewer</option>
                    <option value="operator">Operator</option>
                    <option value="admin">Admin</option>
                  </select>
                </Field>
              </>
            )}
          </div>

          <div className="modal-foot" style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
            <Dialog.Close asChild><button className="btn">Close</button></Dialog.Close>
            {!ok && (
              <button
                className="btn btn-primary"
                onClick={submit}
                disabled={busy || !email.trim()}
              >{busy ? "Inviting…" : "Send invite"}</button>
            )}
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

// ---------- shared bits -----------------------------------------------

const inputStyle: React.CSSProperties = {
  width: "100%", height: 32, padding: "0 10px", fontSize: 13,
  background: "var(--surface)", border: "1px solid var(--border-strong)",
  borderRadius: 3, color: "var(--ink)", fontFamily: "var(--font-sans)",
};

function TextInput({ value, onChange, autoFocus }: {
  value: string; onChange: (v: string) => void; autoFocus?: boolean;
}) {
  return (
    <input
      style={inputStyle}
      value={value}
      autoFocus={autoFocus}
      onChange={(e) => onChange(e.target.value)}
    />
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div style={{ display: "grid", gridTemplateColumns: "130px 1fr", gap: 10, alignItems: "center", marginBottom: 10, fontSize: 13 }}>
      <div className="mute" style={{ fontSize: 11, textTransform: "uppercase", letterSpacing: "0.06em", fontWeight: 500 }}>{label}</div>
      <div>{children}</div>
    </div>
  );
}

function SectionHead({ label, right }: { label: string; right?: React.ReactNode }) {
  return (
    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 10 }}>
      <div style={{ fontSize: 11, fontWeight: 600, letterSpacing: "0.08em", textTransform: "uppercase", color: "var(--muted)" }}>{label}</div>
      {right && <div style={{ display: "flex", gap: 6 }}>{right}</div>}
    </div>
  );
}
