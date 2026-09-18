"use client";

import { useEffect, useMemo, useState } from "react";
import {
  listLocations, createLocation, patchLocation,
  listAliases, addAlias, deleteAlias,
  listLocationPcs, setPcLocation,
  type Location, type Alias, type PcLoc,
} from "../../_lib/admin-locations";

export default function LocationsPage() {
  const [locs, setLocs] = useState<Location[]>([]);
  const [aliases, setAliases] = useState<Alias[]>([]);
  const [pcs, setPcs] = useState<PcLoc[]>([]);
  const [err, setErr] = useState<string | null>(null);

  const load = async () => {
    try {
      const [l, a, p] = await Promise.all([listLocations(), listAliases(), listLocationPcs()]);
      setLocs(l); setAliases(a); setPcs(p);
    } catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
  };
  useEffect(() => { void load(); }, []);

  const locName = useMemo(() => {
    const m = new Map(locs.map((l) => [l.id, l.name]));
    return (id: number) => m.get(id) ?? `#${id}`;
  }, [locs]);

  return (
    <div className="section-pad" style={{ display: "grid", gap: 20, maxWidth: 900 }}>
      <div>
        <h1 style={{ fontSize: 20, fontWeight: 600 }}>Locations</h1>
        <p className="mute" style={{ fontSize: 12.5, marginTop: 4 }}>
          Manage offices, map each lab PC to its office, and map the O365 “Office” values to offices
          (used to auto-assign staff on Microsoft sign-in).
        </p>
      </div>
      {err && <div style={{ padding: "8px 12px", background: "var(--err-soft)", color: "var(--err)", borderRadius: 4, fontSize: 12 }}>{err}</div>}

      <OfficesCard locs={locs} reload={load} onErr={setErr} />
      <PcsCard pcs={pcs} locs={locs} reload={load} onErr={setErr} />
      <AliasesCard aliases={aliases} locs={locs} locName={locName} reload={load} onErr={setErr} />
    </div>
  );
}

function Card({ title, desc, children }: { title: string; desc?: string; children: React.ReactNode }) {
  return (
    <div className="card">
      <div className="card-head"><div className="card-title">{title}</div></div>
      <div className="card-body" style={{ display: "grid", gap: 12 }}>
        {desc && <div className="mute" style={{ fontSize: 12 }}>{desc}</div>}
        {children}
      </div>
    </div>
  );
}

const inp: React.CSSProperties = { height: 32, padding: "0 10px", fontSize: 13, background: "var(--bg)", border: "1px solid var(--border-strong)", borderRadius: 3, color: "var(--ink)" };

function OfficesCard({ locs, reload, onErr }: { locs: Location[]; reload: () => void; onErr: (s: string) => void }) {
  const [code, setCode] = useState("");
  const [name, setName] = useState("");
  const wrap = (fn: () => Promise<unknown>) => async () => { try { await fn(); reload(); } catch (e) { onErr(e instanceof Error ? e.message : String(e)); } };

  return (
    <Card title="OFFICES">
      <table className="tbl">
        <thead><tr><th>Name</th><th>Code</th><th style={{ width: 90 }}>Active</th></tr></thead>
        <tbody>
          {locs.map((l) => (
            <tr key={l.id}>
              <td>{l.name}</td>
              <td className="mono mute">{l.code}</td>
              <td>
                <label style={{ display: "flex", alignItems: "center", gap: 6, cursor: "pointer", fontSize: 12.5 }}>
                  <input type="checkbox" checked={l.active}
                         onChange={wrap(() => patchLocation(l.id, { active: !l.active }))} />
                  {l.active ? "Active" : "Off"}
                </label>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
        <input placeholder="code (e.g. queens)" value={code}
               onChange={(e) => setCode(e.target.value.toLowerCase().replace(/[^a-z0-9_]/g, ""))} style={{ ...inp, width: 150 }} />
        <input placeholder="Display name" value={name} onChange={(e) => setName(e.target.value)} style={{ ...inp, width: 200 }} />
        <button className="btn btn-primary" disabled={!code || !name}
                onClick={wrap(async () => { await createLocation(code, name); setCode(""); setName(""); })}>
          Add office
        </button>
      </div>
    </Card>
  );
}

function PcsCard({ pcs, locs, reload, onErr }: { pcs: PcLoc[]; locs: Location[]; reload: () => void; onErr: (s: string) => void }) {
  return (
    <Card title="LAB PC → OFFICE" desc="Which office each lab PC belongs to. New PDFs scanned from a PC inherit its office.">
      <div style={{ maxHeight: 320, overflow: "auto" }}>
        <table className="tbl">
          <thead><tr><th>PC</th><th>Host</th><th style={{ width: 180 }}>Office</th></tr></thead>
          <tbody>
            {pcs.map((p) => (
              <tr key={p.pc_name}>
                <td>{p.pc_name}</td>
                <td className="mono mute">{p.host}</td>
                <td>
                  <select value={p.location_id} style={{ ...inp, width: "100%" }}
                          onChange={async (e) => {
                            try { await setPcLocation(p.pc_name, Number(e.target.value)); reload(); }
                            catch (err) { onErr(err instanceof Error ? err.message : String(err)); }
                          }}>
                    {locs.map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}
                  </select>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

function AliasesCard({ aliases, locs, locName, reload, onErr }: {
  aliases: Alias[]; locs: Location[]; locName: (id: number) => string; reload: () => void; onErr: (s: string) => void;
}) {
  const [office, setOffice] = useState("");
  const [locId, setLocId] = useState<number>(locs[0]?.id ?? 1);
  useEffect(() => { if (locs[0] && !locId) setLocId(locs[0].id); }, [locs, locId]);
  const wrap = (fn: () => Promise<unknown>) => async () => { try { await fn(); reload(); } catch (e) { onErr(e instanceof Error ? e.message : String(e)); } };

  return (
    <Card title="O365 “OFFICE” → OFFICE MAP"
          desc="When staff sign in with Microsoft, their O365 Office field is matched (case-insensitive) here to pick their office. Add every spelling your org uses.">
      <table className="tbl">
        <thead><tr><th>O365 Office value</th><th style={{ width: 180 }}>Office</th><th style={{ width: 70 }}></th></tr></thead>
        <tbody>
          {aliases.length === 0 && <tr><td colSpan={3} className="mute" style={{ fontSize: 12 }}>No mappings yet — add your O365 Office values below.</td></tr>}
          {aliases.map((a) => (
            <tr key={a.office_value}>
              <td className="mono">{a.office_value}</td>
              <td>{locName(a.location_id)}</td>
              <td><button className="btn" style={{ height: 26, padding: "0 8px", fontSize: 11 }}
                          onClick={wrap(() => deleteAlias(a.office_value))}>Remove</button></td>
            </tr>
          ))}
        </tbody>
      </table>
      <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
        <input placeholder='O365 Office (e.g. "Far Rockaway")' value={office}
               onChange={(e) => setOffice(e.target.value)} style={{ ...inp, width: 240 }} />
        <select value={locId} onChange={(e) => setLocId(Number(e.target.value))} style={{ ...inp, width: 180 }}>
          {locs.map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}
        </select>
        <button className="btn btn-primary" disabled={!office.trim()}
                onClick={wrap(async () => { await addAlias(office, locId); setOffice(""); })}>
          Add mapping
        </button>
      </div>
    </Card>
  );
}
