"use client";

// ponytail: client-side fuzzy over already-loaded pdfs + PCs. No backend, no search index.
// Upgrade path: if the dataset grows past a few thousand rows, add a server-side /api/search.

import * as Dialog from "@radix-ui/react-dialog";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import { useApp } from "./app-provider";
import { IconFiles, IconMonitor, IconSearch } from "./icons";
import { displayName } from "./util";
import { apiFetch } from "../_lib/auth";

// ponytail: same-origin via next.config rewrite; use apiFetch for auth headers.
const API = "";

type PcHit = { kind: "pc"; pc_name: string; host: string };
type PdfHit = { kind: "pdf"; id: number; label: string; sub: string };
type Hit = PcHit | PdfHit;

export function CommandPalette({ open, onOpenChange }: { open: boolean; onOpenChange: (v: boolean) => void }) {
  const { pdfs } = useApp();
  const router = useRouter();
  const setOpen = onOpenChange;
  const [q, setQ] = useState("");
  const [sel, setSel] = useState(0);
  const [pcs, setPcs] = useState<{ pc_name: string; host: string }[]>([]);
  const listRef = useRef<HTMLDivElement>(null);

  // Load PC list once — 24 rows, cheap.
  useEffect(() => {
    apiFetch(`${API}/api/pcs`).then((r) => r.json()).then((rows) => {
      setPcs(rows.map((r: { pc_name: string; host: string }) => ({ pc_name: r.pc_name, host: r.host })));
    }).catch(() => {});
  }, []);

  // Global hotkey: Ctrl/Cmd+K toggles
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setOpen(!open);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, setOpen]);

  const hits: Hit[] = useMemo(() => {
    const term = q.trim().toLowerCase();
    const out: Hit[] = [];
    // PCs: name or host substring
    for (const p of pcs) {
      if (!term || p.pc_name.toLowerCase().includes(term) || p.host.includes(term)) {
        out.push({ kind: "pc", pc_name: p.pc_name, host: p.host });
        if (out.length >= 6 && term) break;
      }
    }
    // PDFs: bag search over metadata
    let pdfCount = 0;
    for (const p of pdfs) {
      // Match the FILES-page bag: prefer the on-share name for committed rows.
      const shownName = p.dest_path?.split("\\").pop() || p.filename;
      const bag = [shownName, p.host, p.first_name, p.last_name, p.assessment_type, p.proposed_name]
        .filter(Boolean).join(" ").toLowerCase();
      if (!term || bag.includes(term)) {
        out.push({
          kind: "pdf",
          id: p.id,
          label: displayName(p),
          sub: `${p.host} · ${(p.assessment_type ?? "unclassified").replace(/_/g, " ")} · ${p.filename}`,
        });
        if (++pdfCount >= 14) break;
      }
    }
    return out;
  }, [q, pdfs, pcs]);

  useEffect(() => { setSel(0); }, [q, open]);

  const pick = (h: Hit) => {
    setOpen(false);
    setQ("");
    if (h.kind === "pc") router.push(`/pcs?open=${encodeURIComponent(h.pc_name)}`);
    else router.push(`/files?open=${h.id}`);
  };

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowDown") { e.preventDefault(); setSel((s) => Math.min(hits.length - 1, s + 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setSel((s) => Math.max(0, s - 1)); }
    else if (e.key === "Enter" && hits[sel]) { e.preventDefault(); pick(hits[sel]); }
  };

  // Scroll selected item into view
  useEffect(() => {
    const el = listRef.current?.querySelector<HTMLElement>(`[data-idx="${sel}"]`);
    el?.scrollIntoView({ block: "nearest" });
  }, [sel]);

  return (
    <Dialog.Root open={open} onOpenChange={setOpen}>
      <Dialog.Portal>
        <Dialog.Overlay className="drawer-overlay" />
        <Dialog.Content
          className="modal"
          style={{ width: "min(620px, 92vw)", top: "20%", transform: "translate(-50%, 0)", maxHeight: "70vh" }}
          aria-describedby={undefined}
          onKeyDown={onKeyDown}
        >
          <Dialog.Title style={{ position: "absolute", left: -9999 }}>Command palette</Dialog.Title>
          <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "14px 18px", borderBottom: "1px solid var(--border)" }}>
            <span style={{ color: "var(--muted)" }}><IconSearch /></span>
            <input
              autoFocus
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Search files & PCs…"
              style={{ flex: 1, background: "transparent", border: "none", outline: "none", fontSize: 14, color: "var(--ink)" }}
            />
            <span className="mono mute" style={{ fontSize: 10, border: "1px solid var(--border-strong)", borderRadius: 3, padding: "2px 6px" }}>ESC</span>
          </div>

          <div ref={listRef} style={{ overflow: "auto", maxHeight: "50vh", padding: "6px 0" }}>
            {hits.length === 0 ? (
              <div style={{ padding: "24px 18px", color: "var(--muted)", fontSize: 13 }}>
                {q.trim() ? "No matches." : "Type to search files, clients, PCs…"}
              </div>
            ) : hits.map((h, i) => (
              <div
                key={h.kind === "pc" ? `pc-${h.pc_name}` : `pdf-${h.id}`}
                data-idx={i}
                onMouseEnter={() => setSel(i)}
                onClick={() => pick(h)}
                style={{
                  display: "flex", alignItems: "center", gap: 10,
                  padding: "8px 18px", cursor: "pointer",
                  background: i === sel ? "var(--accent-soft)" : "transparent",
                  borderLeft: i === sel ? "2px solid var(--accent)" : "2px solid transparent",
                }}
              >
                <span style={{ color: "var(--muted)", width: 16, flexShrink: 0 }}>
                  {h.kind === "pc" ? <IconMonitor /> : <IconFiles />}
                </span>
                {h.kind === "pc" ? (
                  <>
                    <span style={{ fontWeight: 600, fontSize: 13 }}>{h.pc_name}</span>
                    <span className="mono mute" style={{ fontSize: 11 }}>{h.host}</span>
                    <span style={{ marginLeft: "auto", fontSize: 10, color: "var(--muted)", textTransform: "uppercase", letterSpacing: "0.05em" }}>PC</span>
                  </>
                ) : (
                  <div style={{ display: "flex", flexDirection: "column", minWidth: 0, flex: 1 }}>
                    <div style={{ fontSize: 13, fontWeight: 500, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{h.label}</div>
                    <div className="mono mute" style={{ fontSize: 10.5, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{h.sub}</div>
                  </div>
                )}
              </div>
            ))}
          </div>

          <div style={{ display: "flex", gap: 14, padding: "8px 18px", borderTop: "1px solid var(--border)", background: "var(--bg)", fontSize: 10.5, color: "var(--muted)" }}>
            <span><span className="mono">↑↓</span> navigate</span>
            <span><span className="mono">⏎</span> open</span>
            <span style={{ marginLeft: "auto" }}>{hits.length} result{hits.length === 1 ? "" : "s"}</span>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
