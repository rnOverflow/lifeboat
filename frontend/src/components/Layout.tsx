import clsx from "clsx";
import { Activity, Archive, Bot, FlaskConical, GitBranch, LayoutDashboard, LogOut, ScrollText, Settings, Siren, Zap } from "lucide-react";
import { NavLink, Outlet } from "react-router-dom";
import { useAuth } from "../lib/auth";
import { useStatus } from "../lib/queries";
import { Badge, CloudMark } from "./ui";

const NAV = [
  { to: "/", label: "Overview", icon: LayoutDashboard, end: true },
  { to: "/runs", label: "Failover", icon: GitBranch },
  { to: "/simulator", label: "Simulator", icon: Zap },
  { to: "/gamedays", label: "Game Days", icon: FlaskConical },
  { to: "/backups", label: "Backups", icon: Archive },
  { to: "/incidents", label: "Incidents", icon: Siren },
  { to: "/copilot", label: "Copilot", icon: Bot },
  { to: "/audit", label: "Audit", icon: ScrollText, perm: "view_audit" },
  { to: "/settings", label: "Settings", icon: Settings },
];

export default function Layout() {
  const { me, logout, can } = useAuth();
  const { data: st, isError } = useStatus();
  const active = st?.regions.find((r) => r.name === st.active_region);
  const awaiting = st?.active_run?.status === "awaiting_approval";
  return (
    <div className="flex h-full flex-col md:flex-row">
      <aside className="flex shrink-0 flex-col border-b border-ink-800 bg-ink-900 md:w-56 md:border-r md:border-b-0">
        <div className="flex items-center gap-2 px-4 py-4">
          <svg viewBox="0 0 32 32" className="h-7 w-7" aria-hidden>
            <circle cx="16" cy="16" r="12" fill="none" stroke="var(--color-signal)" strokeWidth="5" />
            <circle cx="16" cy="16" r="12" fill="none" stroke="white" strokeWidth="5" strokeDasharray="9.42 9.42" />
          </svg>
          <div>
            <div className="text-[15px] font-bold leading-none tracking-tight">Lifeboat</div>
            <div className="text-[10px] uppercase tracking-widest text-ink-400">DR control plane</div>
          </div>
        </div>
        <nav className="flex gap-1 overflow-x-auto px-2 pb-2 md:flex-col md:overflow-visible">
          {NAV.filter((n) => !n.perm || can(n.perm)).map((n) => (
            <NavLink
              key={n.to}
              to={n.to}
              end={n.end}
              className={({ isActive }) =>
                clsx("flex shrink-0 items-center gap-2.5 rounded-lg px-3 py-2 text-sm transition", isActive ? "bg-ink-800 text-white" : "text-ink-300 hover:bg-ink-850 hover:text-ink-100")
              }
            >
              <n.icon size={16} />
              {n.label}
              {n.to === "/runs" && awaiting && <span className="ml-auto h-2 w-2 rounded-full bg-warn" aria-label="approval needed" />}
            </NavLink>
          ))}
        </nav>
        <div className="mt-auto hidden border-t border-ink-800 p-3 md:block">
          <div className="text-sm font-medium">{me?.username}</div>
          <div className="mb-2 text-xs capitalize text-ink-400">{me?.role}</div>
          <button onClick={logout} className="flex items-center gap-1.5 text-xs text-ink-400 hover:text-ink-100">
            <LogOut size={13} /> Sign out
          </button>
        </div>
      </aside>
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex flex-wrap items-center gap-x-5 gap-y-2 border-b border-ink-800 bg-ink-950/80 px-4 py-2.5 text-sm backdrop-blur md:px-6">
          <div className="flex items-center gap-2">
            <Activity size={15} className={isError ? "text-bad" : "text-ok"} />
            <span className="text-ink-400">Traffic</span>
            {active && <CloudMark cloud={active.cloud} />}
            <span className="font-medium">{active?.display_name ?? "…"}</span>
            {st?.failed_over && <Badge tone="warn">failed over</Badge>}
          </div>
          {st && (
            <div className="flex items-center gap-2">
              <span className="text-ink-400">Readiness</span>
              <span className="font-mono font-semibold tabular">{st.readiness.score}</span>
              <Badge tone={st.readiness.status === "ready" ? "ok" : st.readiness.status === "at_risk" ? "warn" : "bad"}>{st.readiness.grade}</Badge>
            </div>
          )}
          {st && st.open_incidents.filter((i) => i.status === "open").length > 0 && (
            <Badge tone="bad">{st.open_incidents.filter((i) => i.status === "open").length} open incident(s)</Badge>
          )}
          <span className="ml-auto text-xs text-ink-400">
            {st?.mode === "local" ? "Local simulation" : "Cloud mode"} · {me?.username} ({me?.role})
            <button onClick={logout} className="ml-3 text-ink-300 underline md:hidden">sign out</button>
          </span>
        </header>
        <main className="min-w-0 flex-1 overflow-y-auto px-4 py-5 md:px-6">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
