import clsx from "clsx";
import { Database, Globe, Server, Wifi } from "lucide-react";
import type { Region, Status } from "../lib/api";
import { secs } from "../lib/format";
import { CloudMark, StatusBadge } from "./ui";

function Comp({ ok, icon: Icon, label, detail }: { ok?: boolean; icon: typeof Server; label: string; detail?: string }) {
  return (
    <div className="flex items-center gap-2 text-xs" title={detail}>
      <Icon size={13} className={ok ? "text-ok" : "text-bad"} />
      <span className="text-ink-300">{label}</span>
      <span className={clsx("ml-auto truncate font-mono", ok ? "text-ink-400" : "text-bad")}>{ok ? detail || "ok" : detail || "fail"}</span>
    </div>
  );
}

function RegionCard({ r, active }: { r: Region; active: boolean }) {
  const down = r.status === "down";
  return (
    <div
      className={clsx(
        "relative rounded-xl border p-4",
        down ? "border-bad/60 bg-bad/5" : r.status === "degraded" ? "border-warn/50 bg-warn/5" : "border-ink-700 bg-ink-850",
        active && !down && "ring-2 ring-ok/50",
      )}
    >
      <div className="flex items-center gap-2">
        <CloudMark cloud={r.cloud} />
        <div className="min-w-0">
          <div className="truncate font-semibold">{r.display_name}</div>
          <div className="text-[11px] uppercase tracking-wider text-ink-400">designated {r.designated_role}</div>
        </div>
        <div className={clsx("ml-auto rounded-full", down && "pulse-bad")}>
          <StatusBadge value={r.status} />
        </div>
      </div>
      <div className="mt-3 space-y-1.5">
        <Comp ok={r.probe.app?.ok} icon={Server} label="App tier" detail={r.probe.app?.detail} />
        <Comp ok={r.probe.database?.ok} icon={Database} label="Database" detail={r.probe.database?.detail} />
        <Comp ok={r.probe.network?.ok} icon={Wifi} label="Network" detail={r.probe.network?.ok ? `${r.probe.latency_ms?.toFixed(0)} ms` : r.probe.network?.detail} />
      </div>
      <div className="mt-3 flex items-center justify-between border-t border-ink-700 pt-2 text-xs">
        <span className="text-ink-400">DB role</span>
        <StatusBadge value={r.db_role} />
      </div>
      <div className="mt-1.5 flex items-center justify-between text-xs">
        <span className="text-ink-400">Last applied txn</span>
        <span className="font-mono tabular">#{r.last_applied_seq ?? "—"}</span>
      </div>
      {active && <div className="absolute -top-2.5 left-4 rounded bg-ok px-1.5 text-[10px] font-bold uppercase tracking-wider text-ink-950">serving traffic</div>}
    </div>
  );
}

export default function Topology({ st }: { st: Status }) {
  const [a, b] = st.regions;
  const primary = st.regions.find((r) => r.db_role === "primary");
  const replica = st.regions.find((r) => r.db_role === "replica");
  const lag = replica?.replication_lag_s;
  const flowing = primary && replica && primary.status === "up" && replica.status === "up" && lag !== null && lag !== undefined;
  const leftToRight = primary?.name === a.name;
  const lagTone = lag == null ? "text-ink-400" : lag <= st.policy.max_replication_lag_s ? "text-ok" : lag <= st.policy.rpo_target_s ? "text-warn" : "text-bad";
  return (
    <div>
      <div className="mb-4 flex items-center justify-center gap-2 text-sm text-ink-300">
        <Globe size={16} className="text-azure" />
        <span>Global DNS</span>
        <span className="font-mono text-ink-400">app.lifeboat.example</span>
        <span>→</span>
        <span className="font-medium text-ink-100">{st.regions.find((r) => r.name === st.active_region)?.display_name}</span>
      </div>
      <div className="grid items-center gap-3 md:grid-cols-[minmax(0,1fr)_9rem_minmax(0,1fr)]">
        <RegionCard r={a} active={st.active_region === a.name} />
        <div className="flex flex-col items-center py-2 text-center">
          <svg viewBox="0 0 140 24" className="w-full max-w-36" aria-hidden>
            <line x1="6" y1="12" x2="134" y2="12" stroke="var(--color-ink-600)" strokeWidth="2" />
            {flowing && <line x1="6" y1="12" x2="134" y2="12" stroke="var(--color-azure)" strokeWidth="2.5" className="flow" style={{ animationDirection: leftToRight ? "normal" : "reverse" }} />}
            {flowing && (leftToRight ? <path d="M126 6 L134 12 L126 18" fill="none" stroke="var(--color-azure)" strokeWidth="2" /> : <path d="M14 6 L6 12 L14 18" fill="none" stroke="var(--color-azure)" strokeWidth="2" />)}
          </svg>
          <div className="mt-1 text-[11px] uppercase tracking-wider text-ink-400">async replication</div>
          <div className={clsx("font-mono text-sm font-semibold tabular", lagTone)}>{flowing ? `lag ${secs(lag)}` : "not replicating"}</div>
        </div>
        <RegionCard r={b} active={st.active_region === b.name} />
      </div>
    </div>
  );
}
