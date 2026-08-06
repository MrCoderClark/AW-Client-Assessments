"use client";

// Renders a dashboard from a list of widget keys. Used both for custom
// per-user compositions (from users.dashboard_widgets) and for profile
// defaults (see widgetsForProfile).

import { PdfDrawer } from "../pdf-drawer";
import { useApp } from "../app-provider";
import { useState } from "react";
import { WIDGETS } from "./widgets";

export function CustomDashboard({ widgets }: { widgets: string[] }) {
  const { pdfs } = useApp();
  const [selectedId, setSelectedId] = useState<number | null>(null);

  const stats: string[] = [];
  const cards: string[] = [];
  for (const key of widgets) {
    const def = WIDGETS[key];
    if (!def) continue;
    if (def.kind === "stat") stats.push(key);
    else cards.push(key);
  }

  // 4-col grid for stats; 2-col for cards. When there's just one card,
  // let it span the row so we don't get a lopsided layout.
  const cardsCls = cards.length === 1 ? "" : "dash-grid";

  return (
    <div className="section-pad">
      {stats.length > 0 && (
        <div className="stat-grid">
          {stats.map((k) => {
            const W = WIDGETS[k].component;
            return <W key={k} />;
          })}
        </div>
      )}
      {cards.length > 0 && (
        <div className={cardsCls}>
          {cards.map((k) => {
            const W = WIDGETS[k].component;
            return <W key={k} />;
          })}
        </div>
      )}
      {stats.length === 0 && cards.length === 0 && (
        <div className="empty" style={{ padding: "60px 20px" }}>
          <h3>No widgets yet</h3>
          <p>Click Customize on the top bar to add widgets to this dashboard.</p>
        </div>
      )}

      <PdfDrawer
        pdf={selectedId ? (pdfs.find((p) => p.id === selectedId) ?? null) : null}
        open={selectedId !== null}
        onClose={() => setSelectedId(null)}
      />
    </div>
  );
}
