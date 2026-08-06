"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { IconArchive, IconDashboard, IconFiles, IconLayout, IconLogs, IconMonitor, IconSettings, IconUsers } from "./icons";
import { Logo } from "./logo";
import { useApp } from "./app-provider";
import { useAuth } from "./auth-provider";

// `hiddenForRoles` hides an entry from users whose `me.role` is in the list,
// on top of the permission check. Viewers don't see the technical fleet
// pages even though they have `pc:read`/`log:read` at the API layer — that
// access is left in place because it's what the pages they DO see use.
type NavItem = {
  href: string;
  label: string;
  icon: typeof IconDashboard;
  enabled: boolean;
  perm: string | null;
  hiddenForRoles: string[];
};

const NAV: NavItem[] = [
  { href: "/",         label: "Dashboard", icon: IconDashboard, enabled: true, perm: null, hiddenForRoles: [] },
  { href: "/files",    label: "Files",     icon: IconFiles,     enabled: true, perm: null, hiddenForRoles: [] },
  { href: "/pcs",      label: "PCs",       icon: IconMonitor,   enabled: true, perm: null, hiddenForRoles: ["viewer"] },
  { href: "/logs",     label: "Logs",      icon: IconLogs,      enabled: true, perm: null, hiddenForRoles: ["viewer"] },
  { href: "/admin/users",    label: "Users",    icon: IconUsers,   enabled: true, perm: "user:read", hiddenForRoles: [] },
  { href: "/admin/profiles", label: "Profiles", icon: IconLayout,  enabled: true, perm: "user:write", hiddenForRoles: [] },
  { href: "/admin/archive",  label: "Archive",  icon: IconArchive, enabled: true, perm: "pdf:archive", hiddenForRoles: [] },
  { href: "/settings",       label: "Settings", icon: IconSettings, enabled: true, perm: "schedule:write", hiddenForRoles: [] },
];

export function Sidebar() {
  const pathname = usePathname();
  const { apiReachable } = useApp();
  const { me } = useAuth();
  const perms = new Set(me?.permissions ?? []);
  return (
    <aside className="sidebar">
      <div className="sidebar-brand">
        <Logo size={28} />
        <div>
          <div className="sidebar-name">Client Viewer</div>
          <div className="sidebar-sub">Assessments</div>
        </div>
      </div>

      <nav className="sidebar-nav">
        {NAV
          .filter(n => !n.perm || perms.has(n.perm))
          .filter(n => !n.hiddenForRoles.includes(me?.role ?? ""))
          .map(({ href, label, icon: Icon, enabled }) => {
          const active = pathname === href || (href !== "/" && pathname.startsWith(href + "/"));
          const cls = `nav-item${active ? " active" : ""}`;
          const inner = (
            <>
              <Icon />
              <span>{label}</span>
              {!enabled && <span style={{ marginLeft: "auto", fontSize: 9, color: "var(--sidebar-muted)", letterSpacing: "0.08em" }}>SOON</span>}
            </>
          );
          return enabled ? (
            <Link key={href} href={href} className={cls}>{inner}</Link>
          ) : (
            <div key={href} className={cls} style={{ cursor: "not-allowed", opacity: 0.55 }}>{inner}</div>
          );
        })}
      </nav>

      <div className="sidebar-foot">
        <span className={`status-dot${apiReachable === false ? " off" : ""}`} />
        <span>{apiReachable === false ? "API unreachable" : apiReachable === true ? "API connected" : "Connecting…"}</span>
      </div>
    </aside>
  );
}
