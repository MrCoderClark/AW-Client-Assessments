"use client";

// Dashboard router.
// 1. If the user has set `dashboard_widgets` (custom composition), render
//    those via CustomDashboard.
// 2. Otherwise render the widgets bound to their profile's layout_key.
// A floating "Customize" button opens a drawer to change the composition.

import { useState } from "react";
import { useAuth } from "./_components/auth-provider";
import { CustomDashboard } from "./_components/dashboards/custom";
import { CustomizeDrawer } from "./_components/dashboards/customize-drawer";
import { widgetsForProfile } from "./_components/dashboards/widgets";
import { IconSettings } from "./_components/icons";

export default function Dashboard() {
  const { me } = useAuth();
  const [customizeOpen, setCustomizeOpen] = useState(false);

  const widgets =
    me?.dashboard_widgets && me.dashboard_widgets.length >= 0
      ? me.dashboard_widgets
      : widgetsForProfile(me?.profile?.layout_key);

  return (
    <>
      <CustomDashboard widgets={widgets} />

      <button
        onClick={() => setCustomizeOpen(true)}
        title="Customize dashboard widgets"
        style={{
          position: "fixed", right: 24, bottom: 24, zIndex: 40,
          display: "flex", alignItems: "center", gap: 8,
          padding: "10px 16px",
          background: "var(--accent)", color: "white",
          border: "none", borderRadius: 999,
          boxShadow: "0 2px 12px rgba(0,0,0,0.18)",
          fontSize: 12, fontWeight: 600, cursor: "pointer",
        }}
      >
        <IconSettings /> Customize
      </button>

      <CustomizeDrawer open={customizeOpen} onClose={() => setCustomizeOpen(false)} />
    </>
  );
}
