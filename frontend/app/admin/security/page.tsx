"use client";

// Admin-only Access & Security page: SSO on/off, SSO allowlist (searchable,
// paginated, bulk add), and the global 2FA requirement. Split out of /settings
// to keep that page focused on scheduling.

import { useCallback, useEffect, useState } from "react";
import { RequirePerm } from "../../_components/require-perm";
import { IconSearch } from "../../_components/icons";
import {
  AllowEntry, AppSettings, addAllowlist, getAllowlist, getSettings,
  patchSettings, removeAllowlist,
} from "../../_lib/admin-settings";

const PAGE = 50;

export default function SecurityPage() {
  return (
    <RequirePerm perms={["system:write"]}>
      <SecurityInner />
    </RequirePerm>
  );
}

function SecurityInner() {
  const [s, setS] = useState<AppSettings | null>(null);

  useEffect(() => { getSettings().then(setS).catch(() => {}); }, []);

  const set = async (key: keyof AppSettings, value: boolean) => {
    const prev = s;
    setS((cur) => (cur ? { ...cur, [key]: value } : cur));
    try { setS(await patchSettings({ [key]: value })); }
    catch { setS(prev); }
  };

  return (
    <div className="section-pad" style={{ maxWidth: 820 }}>
      <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
        <div className="card">
          <div className="card-head"><div className="card-title">SIGN-IN</div></div>
          <div className="card-body" style={{ display: "flex", flexDirection: "column", gap: 20 }}>
            <Row
              title="Local accounts only"
              desc="When on, Microsoft (SSO) sign-in is blocked — only local accounts can log in, and no new SSO accounts are created. Break-glass for an Entra outage or lockdown."
            >
              {s && <Toggle checked={!s.sso_login_enabled} onChange={(on) => set("sso_login_enabled", !on)} />}
            </Row>
            <Divider />
            <Row
              title="Require two-factor (2FA)"
              desc="When on, every user must set up an authenticator app on their next sign-in (unless individually exempted on the Users page). Existing sessions stay valid until they expire."
            >
              {s && <Toggle checked={s.mfa_required} onChange={(on) => set("mfa_required", on)} />}
            </Row>
          </div>
        </div>

        <div className="card">
          <div className="card-head"><div className="card-title">SSO ALLOWLIST</div></div>
          <div className="card-body" style={{ display: "flex", flexDirection: "column", gap: 18 }}>
            <Row
              title="Restrict Microsoft sign-in to an allowlist"
              desc="When on, only the email addresses below may sign in with Microsoft. Everyone else sees a “contact IT Support” message. Local accounts are unaffected."
            >
              {s && <Toggle checked={s.sso_allowlist_enabled} onChange={(on) => set("sso_allowlist_enabled", on)} />}
            </Row>
            {s && <Allowlist enforced={s.sso_allowlist_enabled} />}
          </div>
        </div>
      </div>
    </div>
  );
}

function Allowlist({ enforced }: { enforced: boolean }) {
  const [rows, setRows] = useState<AllowEntry[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [q, setQ] = useState("");
  const [bulk, setBulk] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(async (nextQ: string, nextOffset: number) => {
    try {
      const p = await getAllowlist({ q: nextQ, limit: PAGE, offset: nextOffset });
      setRows(p.emails); setTotal(p.total); setOffset(p.offset);
    } catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
  }, []);

  // Debounced search; reload on query change.
  useEffect(() => {
    const t = setTimeout(() => load(q, 0), 250);
    return () => clearTimeout(t);
  }, [q, load]);

  const add = async () => {
    const emails = bulk.split(/[\s,;]+/).map((e) => e.trim()).filter(Boolean);
    if (!emails.length) return;
    setBusy(true); setErr(null); setMsg(null);
    try {
      const r = await addAllowlist(emails);
      const bits = [`${r.added} added`];
      if (r.skipped) bits.push(`${r.skipped} already present`);
      if (r.invalid.length) bits.push(`${r.invalid.length} invalid`);
      setMsg(bits.join(" · "));
      setBulk("");
      await load(q, 0);
    } catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  };

  const remove = async (email: string) => {
    try {
      await removeAllowlist(email);
      // if we removed the last row on a page, step back a page
      const nextOffset = rows.length === 1 && offset > 0 ? offset - PAGE : offset;
      await load(q, nextOffset);
    } catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
  };

  const from = total === 0 ? 0 : offset + 1;
  const to = Math.min(offset + rows.length, total);

  return (
    <div style={{ opacity: enforced ? 1 : 0.6 }}>
      {enforced && total === 0 && !q && (
        <div style={{ fontSize: 12, color: "var(--err)", marginBottom: 12 }}>
          The list is empty — no one can sign in with Microsoft until you add an address.
        </div>
      )}

      {/* Bulk add */}
      <label style={labelStyle}>Add addresses (paste one per line, or comma-separated)</label>
      <textarea
        value={bulk}
        onChange={(e) => setBulk(e.target.value)}
        placeholder={"jane@americaworks.com\njohn@americaworks.com"}
        rows={3}
        style={{
          width: "100%", padding: "8px 10px", fontSize: 13, fontFamily: "var(--font-mono)",
          background: "var(--bg)", border: "1px solid var(--border-strong)", borderRadius: 4,
          color: "var(--ink)", resize: "vertical",
        }}
      />
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginTop: 8 }}>
        <button className="btn btn-primary" onClick={add} disabled={busy || !bulk.trim()} style={{ height: 32 }}>
          {busy ? "Adding…" : "Add to allowlist"}
        </button>
        {msg && <span className="mute" style={{ fontSize: 12 }}>{msg}</span>}
        {err && <span style={{ fontSize: 12, color: "var(--err)" }}>{err}</span>}
      </div>

      {/* Search + count */}
      <div style={{ display: "flex", alignItems: "center", gap: 8, margin: "18px 0 10px" }}>
        <div style={{ position: "relative", flex: 1 }}>
          <span style={{ position: "absolute", left: 10, top: "50%", transform: "translateY(-50%)", color: "var(--muted)", display: "flex" }}>
            <IconSearch />
          </span>
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Search allowed emails…"
            style={{
              width: "100%", height: 34, padding: "0 10px 0 34px", fontSize: 13,
              background: "var(--bg)", border: "1px solid var(--border-strong)",
              borderRadius: 4, color: "var(--ink)",
            }}
          />
        </div>
        <span className="mono mute" style={{ fontSize: 12, whiteSpace: "nowrap" }}>
          {total === 0 ? "0" : `${from}–${to} of ${total}`}
        </span>
      </div>

      {/* List */}
      <div style={{ border: "1px solid var(--border)", borderRadius: 6, overflow: "hidden", minHeight: 40 }}>
        {rows.length === 0 ? (
          <div className="mute" style={{ padding: "14px 12px", fontSize: 13 }}>
            {q ? "No matches." : "No addresses yet."}
          </div>
        ) : rows.map((a) => (
          <div key={a.email} style={{
            display: "flex", alignItems: "center", justifyContent: "space-between",
            padding: "8px 12px", borderBottom: "1px solid var(--border)", fontSize: 13,
          }}>
            <span className="mono">{a.email}</span>
            <button onClick={() => remove(a.email)}
              style={{ background: "none", border: "none", color: "var(--err)", cursor: "pointer", fontSize: 12 }}>
              Remove
            </button>
          </div>
        ))}
      </div>

      {/* Pagination */}
      {total > PAGE && (
        <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 10 }}>
          <button className="btn" disabled={offset === 0} onClick={() => load(q, Math.max(0, offset - PAGE))}
            style={{ height: 30 }}>Prev</button>
          <button className="btn" disabled={to >= total} onClick={() => load(q, offset + PAGE)}
            style={{ height: 30 }}>Next</button>
        </div>
      )}
    </div>
  );
}

function Row({ title, desc, children }: { title: string; desc: string; children: React.ReactNode }) {
  return (
    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 16 }}>
      <div>
        <div style={{ fontSize: 13, fontWeight: 500 }}>{title}</div>
        <div className="mute" style={{ fontSize: 12 }}>{desc}</div>
      </div>
      {children}
    </div>
  );
}

function Divider() {
  return <div style={{ height: 1, background: "var(--border)" }} />;
}

const labelStyle: React.CSSProperties = {
  display: "block", fontSize: 11.5, color: "var(--muted)",
  marginBottom: 6, letterSpacing: "0.02em", textTransform: "uppercase",
};

function Toggle({ checked, onChange }: { checked: boolean; onChange: (on: boolean) => void }) {
  return (
    <label style={{ position: "relative", display: "inline-block", width: 40, height: 22, flex: "0 0 auto" }}>
      <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)}
             style={{ opacity: 0, width: 0, height: 0 }} />
      <span style={{
        position: "absolute", cursor: "pointer", inset: 0,
        background: checked ? "var(--accent)" : "var(--border-strong)",
        borderRadius: 22, transition: "background 120ms",
      }}>
        <span style={{
          position: "absolute", top: 2, left: checked ? 20 : 2,
          width: 18, height: 18, background: "white", borderRadius: 18, transition: "left 120ms",
        }} />
      </span>
    </label>
  );
}
