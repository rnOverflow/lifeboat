import clsx from "clsx";
import { Cloud, Database, Network, RefreshCw, Server, Unplug } from "lucide-react";
import { Button, Card, CloudMark, Empty, ErrorNote, StatusBadge } from "../components/ui";
import { api, type FaultType } from "../lib/api";
import { useAuth } from "../lib/auth";
import { ago, label } from "../lib/format";
import { useAction, useFaults, useStatus } from "../lib/queries";

const FAULTS: { type: FaultType; icon: typeof Cloud; title: string; effect: string }[] = [
  { type: "cloud_outage", icon: Cloud, title: "Cloud outage", effect: "App, database and network all unavailable" },
  { type: "database_failure", icon: Database, title: "Database failure", effect: "Database refuses connections; replication stops" },
  { type: "network_failure", icon: Network, title: "Network failure", effect: "Region unreachable from monitors and peers" },
  { type: "service_failure", icon: Server, title: "Service failure", effect: "Application tier returns 503; self-heal will restart it" },
  { type: "replication_failure", icon: Unplug, title: "Replication failure", effect: "Replication link broken; lag and RPO exposure grow" },
];

export default function Simulator() {
  const { data: st } = useStatus();
  const { data: faults } = useFaults();
  const { can } = useAuth();
  const inject = useAction((v: { region: string; fault_type: FaultType }) => api("/simulator/faults", { method: "POST", body: v }));
  const clear = useAction((v: { region: string; fault_type?: FaultType }) => api("/simulator/clear", { method: "POST", body: v }));
  const restart = useAction((region: string) => api(`/regions/${region}/restart`, { method: "POST" }));
  const allowed = can("inject_fault");
  if (!st) return <Empty>Loading…</Empty>;
  if (st.mode !== "local") return <Empty>The failure simulator is only available in local provider mode.</Empty>;
  const active = (faults ?? []).filter((f) => f.active);

  return (
    <div className="mx-auto max-w-7xl space-y-5">
      <p className="max-w-3xl text-sm text-ink-400">
        Faults change the real state of the simulated regions. The monitor has to <em>detect</em> them through probes, just as it would in the cloud.
        It never reads this fault table.
      </p>
      {!allowed && <div className="rounded-lg bg-ink-850 px-3 py-2 text-sm text-ink-400">Your role is read-only here. Operators and admins can inject faults.</div>}
      <ErrorNote error={inject.error || clear.error || restart.error} />
      <div className="grid gap-5 lg:grid-cols-2">
        {st.regions.map((r) => {
          const mine = active.filter((f) => f.region === r.name);
          return (
            <Card key={r.name} title={<span className="flex items-center gap-2 normal-case tracking-normal"><CloudMark cloud={r.cloud} /> {r.display_name}</span>} action={<StatusBadge value={r.status} />}>
              <div className="grid gap-2">
                {FAULTS.map((f) => {
                  const on = mine.some((x) => x.fault_type === f.type);
                  return (
                    <div key={f.type} className={clsx("flex items-center gap-3 rounded-lg border px-3 py-2", on ? "border-bad/50 bg-bad/10" : "border-ink-700")}>
                      <f.icon size={16} className={on ? "text-bad" : "text-ink-400"} />
                      <div className="min-w-0 flex-1">
                        <div className="text-sm font-medium">{f.title}</div>
                        <div className="text-xs text-ink-400">{f.effect}</div>
                      </div>
                      <Button
                        variant={on ? "default" : "danger"}
                        disabled={!allowed || inject.isPending || clear.isPending}
                        onClick={() => (on ? clear.mutate({ region: r.name, fault_type: f.type }) : inject.mutate({ region: r.name, fault_type: f.type }))}
                        aria-pressed={on}
                      >
                        {on ? "Recover" : "Inject"}
                      </Button>
                    </div>
                  );
                })}
              </div>
              <div className="mt-3 flex flex-wrap gap-2">
                <Button variant="ghost" disabled={!allowed || mine.length === 0} onClick={() => clear.mutate({ region: r.name })}>Recover all faults</Button>
                <Button variant="ghost" disabled={!can("remediate")} onClick={() => restart.mutate(r.name)}><RefreshCw size={14} /> Restart service</Button>
              </div>
            </Card>
          );
        })}
      </div>
      <Card title="Fault history">
        {!faults?.length ? <Empty>No faults injected yet.</Empty> : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-left text-xs uppercase tracking-wider text-ink-400">
                <tr><th className="py-1.5 pr-3">Region</th><th className="pr-3">Fault</th><th className="pr-3">State</th><th className="pr-3">Injected</th><th className="pr-3">By</th><th>Cleared</th></tr>
              </thead>
              <tbody className="divide-y divide-ink-800">
                {faults.map((f) => (
                  <tr key={f.id}>
                    <td className="py-1.5 pr-3">{f.region}</td>
                    <td className="pr-3 capitalize">{label(f.fault_type)}</td>
                    <td className="pr-3">{f.active ? <StatusBadge value="open" /> : <StatusBadge value="resolved" />}</td>
                    <td className="pr-3 text-ink-400">{ago(f.injected_at)}</td>
                    <td className="pr-3 text-ink-400">{f.injected_by}</td>
                    <td className="text-ink-400">{f.cleared_at ? ago(f.cleared_at) : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}
