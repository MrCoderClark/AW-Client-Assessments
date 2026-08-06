"use client";

// Viewer-friendly "assessments grouped by type" page. Landing target of
// the Records dashboard's Assessment Types tile. Renders one section per
// distinct assessment_type, with the files under each. Clicking a row
// opens the existing PdfDrawer.

import { useMemo, useState } from "react";
import { useApp } from "../_components/app-provider";
import { PdfDrawer } from "../_components/pdf-drawer";
import { displayName, fmtRelative, formatAssessmentType, ftypeClass, ftypeLabel } from "../_components/util";

type Group = {
  key: string;               // raw assessment_type or "" for unclassified
  label: string;             // display name
  count: number;
  ids: number[];             // pdf ids, newest first
};

const PREVIEW_ROWS = 8;

export default function AssessmentsPage() {
  const { pdfs, loading } = useApp();
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  const groups: Group[] = useMemo(() => {
    const buckets = new Map<string, number[]>();
    for (const p of pdfs) {
      const k = p.assessment_type ?? "";
      const arr = buckets.get(k) ?? [];
      arr.push(p.id);
      buckets.set(k, arr);
    }
    return [...buckets.entries()]
      .map(([key, ids]) => ({
        key,
        label: formatAssessmentType(key || null),
        count: ids.length,
        ids,
      }))
      .sort((a, b) => b.count - a.count);
  }, [pdfs]);

  const toggle = (key: string) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key); else next.add(key);
      return next;
    });

  const selectedPdf =
    selectedId !== null ? pdfs.find((p) => p.id === selectedId) ?? null : null;

  return (
    <div className="section-pad">
      <div style={{ marginBottom: 18 }}>
        <h1 style={{ fontSize: 20, fontWeight: 600, letterSpacing: "-0.01em", margin: 0 }}>
          Assessments by type
        </h1>
        <div className="mute" style={{ fontSize: 12, marginTop: 4 }}>
          {loading
            ? "Loading…"
            : `${groups.length} type${groups.length === 1 ? "" : "s"} · ${pdfs.length} assessment${pdfs.length === 1 ? "" : "s"}`}
        </div>
      </div>

      {groups.length === 0 && !loading ? (
        <div className="empty" style={{ padding: "60px 20px" }}>
          <h3>No assessments yet</h3>
          <p>Nothing has been indexed.</p>
        </div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          {groups.map((g) => {
            const isOpen = expanded.has(g.key);
            const previewIds = isOpen ? g.ids : g.ids.slice(0, PREVIEW_ROWS);
            const remaining = g.count - previewIds.length;
            return (
              <div key={g.key || "unclassified"} className="card">
                <div
                  className="card-head"
                  onClick={() => toggle(g.key)}
                  style={{ cursor: "pointer", userSelect: "none" }}
                >
                  <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
                    <span className={`ftype ${ftypeClass(g.key || null)}`}>{ftypeLabel(g.key || null)}</span>
                    <div>
                      <div className="card-title" style={{ letterSpacing: "-0.005em" }}>
                        {g.label}
                      </div>
                      <div className="mono mute" style={{ fontSize: 11, marginTop: 2 }}>
                        {g.count} assessment{g.count === 1 ? "" : "s"}
                      </div>
                    </div>
                  </div>
                  <span className="mute" style={{ fontSize: 16 }}>{isOpen ? "▾" : "▸"}</span>
                </div>
                <table className="tbl">
                  <thead>
                    <tr>
                      <th style={{ paddingLeft: 18 }}>Client</th>
                      <th style={{ width: 160 }}>Received</th>
                    </tr>
                  </thead>
                  <tbody>
                    {previewIds.map((id) => {
                      const p = pdfs.find((x) => x.id === id);
                      if (!p) return null;
                      return (
                        <tr key={id} onClick={() => setSelectedId(id)} style={{ cursor: "pointer" }}>
                          <td style={{ paddingLeft: 18 }}>{displayName(p)}</td>
                          <td className="mono mute">{fmtRelative(p.indexed_at)}</td>
                        </tr>
                      );
                    })}
                    {!isOpen && remaining > 0 && (
                      <tr onClick={() => toggle(g.key)} style={{ cursor: "pointer" }}>
                        <td colSpan={2} style={{ paddingLeft: 18, color: "var(--muted)", fontSize: 12 }}>
                          Show {remaining} more →
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            );
          })}
        </div>
      )}

      <PdfDrawer
        pdf={selectedPdf}
        open={selectedPdf !== null}
        onClose={() => setSelectedId(null)}
      />
    </div>
  );
}
