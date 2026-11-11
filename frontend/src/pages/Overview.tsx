import { AlertTriangle, ArrowRight, CheckCircle2, XCircle } from "lucide-react";
import { Link } from "react-router-dom";
import { Area, AreaChart, CartesianGrid, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import Gauge from "../components/Gauge";
import Topology from "../components/Topology";
import { Badge, Card, Empty, Stat, StatusBadge } from "../components/ui";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";
import { secs, time } from "../lib/format";
import { useAction, useHistory, usePlan, useStatus } from "../lib/queries";

export default function Overview() {
  const { data: st, error } = useStatus();
  const { data: plan } = usePlan();
  const { can } = useAuth();
  const standby = st?.standby_region;
  const { data: hist } = useHistory(standby, 10);
  const approve = useAction((id: number) => api(`/runs/${id}/approve`, { method: "POST", body: { comment: "approved from overview" } }));

  if (error) return <Empty>Cannot reach the API: {(error as Error).message}</Empty>;
  if (!st) return <Empty>Loading…</Empty>;
  const p = st.policy;
  const exposure = st.rpo_exposure_s;
  const lastRto = st.measured_rto_s.at(-1);
  const rd = st.readiness;
  const wl = st.workload;
  const lagData = (hist ?? []).map((h) => ({ t: new Date(h.ts).toLocaleTimeString([], { minute: "2-digit", second: "2-digit" }), lag: h.replication_lag_s }));

  return (
    <div className="mx-auto max-w-7xl space-y-5">
      {st.active_run && (
        <div className="flex flex-wrap items-center gap-3 rounded-xl border border-warn/40 bg-warn/10 px-4 py-3">
          <AlertTriangle className="text-warn" size={18} />
          <div className="text-sm">
            <span className="font-semibold capitalize">{st.active_run.kind} run #{st.active_run.id}</span> is{" "}
            <StatusBadge value={st.active_run.status} />
          </div>
          <div className="ml-auto flex gap-2">
            {st.active_run.status === "awaiting_approval" && can("approve_run") && (
              <button
                onClick={() => approve.mutate(st.active_run!.id)}
                disabled={approve.isPending}
                className="rounded-lg bg-warn px-3 py-1.5 text-sm font-semibold text-ink-950 hover:bg-warn/85 disabled:opacity-50"
              >
                Approve
              </button>
            )}
            <Link to={`/runs?id=${st.active_run.id}`} className="flex items-center gap-1 rounded-lg px-3 py-1.5 text-sm text-ink-100 ring-1 ring-ink-600 hover:bg-ink-800">
              Open run <ArrowRight size={14} />
            </Link>
          </div>
          {approve.error && <div className="w-full text-sm text-bad">{(approve.error as Error).message}</div>}
        </div>
      )}

      <Card title="Topology">
        <Topology st={st} />
      </Card>

      <div className="grid gap-5 lg:grid-cols-3">
        <Card title="Recovery readiness">
          <div className="flex items-center gap-4">
            <Gauge value={rd.score} grade={rd.grade} tone={rd.status === "ready" ? "ok" : rd.status === "at_risk" ? "warn" : "bad"} />
            <div className="text-xs text-ink-400">
              Protecting <span className="text-ink-100">{rd.protected_region}</span> with standby{" "}
              <span className="text-ink-100">{rd.standby_region}</span>
            </div>
          </div>
          <ul className="mt-3 space-y-2">
            {rd.components.map((c) => (
              <li key={c.key} className="text-xs">
                <div className="flex justify-between">
                  <span className="text-ink-300">{c.label}</span>
                  <span className="font-mono tabular">
                    {c.score}/{c.weight}
                  </span>
                </div>
                <div className="mt-1 h-1.5 overflow-hidden rounded bg-ink-800">
                  <div className={c.score / c.weight >= 0.8 ? "h-full bg-ok" : c.score / c.weight >= 0.4 ? "h-full bg-warn" : "h-full bg-bad"} style={{ width: `${(c.score / c.weight) * 100}%` }} />
                </div>
                <div className="mt-0.5 text-ink-400">{c.detail}</div>
              </li>
            ))}
          </ul>
          {rd.blockers.length > 0 && (
            <ul className="mt-3 space-y-1 rounded-lg bg-bad/10 p-2 text-xs text-bad">
              {rd.blockers.map((b) => (
                <li key={b}>• {b}</li>
              ))}
            </ul>
          )}
        </Card>

        <Card title="RTO / RPO">
          <div className="grid grid-cols-2 gap-4">
            <Stat label="RTO target" value={secs(p.rto_target_s, 0)} />
            <Stat
              label="Last measured RTO"
              value={secs(lastRto)}
              tone={lastRto === undefined ? undefined : lastRto <= p.rto_target_s ? "ok" : "bad"}
              sub={lastRto === undefined ? "no failover yet" : lastRto <= p.rto_target_s ? "within target" : "breached"}
            />
            <Stat label="RPO target" value={secs(p.rpo_target_s, 0)} />
            <Stat
              label="RPO exposure now"
              value={secs(exposure)}
              tone={exposure === null ? "warn" : exposure <= p.max_replication_lag_s ? "ok" : exposure <= p.rpo_target_s ? "warn" : "bad"}
              sub="data at risk if active region fails now"
            />
          </div>
          <div className="mt-4 space-y-1.5 border-t border-ink-800 pt-3 text-xs">
            {[
              ["Auto-initiate failover on regional failure", p.auto_initiate_failover],
              ["Human approval required", p.require_approval],
              ["Two-person rule", p.two_person_rule],
            ].map(([l, v]) => (
              <div key={l as string} className="flex items-center gap-2">
                {v ? <CheckCircle2 size={13} className="text-ok" /> : <XCircle size={13} className="text-ink-400" />}
                <span className="text-ink-300">{l}</span>
              </div>
            ))}
          </div>
        </Card>

        <Card title="Customer traffic (synthetic)">
          <div className="grid grid-cols-2 gap-4">
            <Stat label="Successful writes" value={wl.ok ?? 0} tone="ok" />
            <Stat label="Failed writes" value={wl.failed ?? 0} tone={(wl.failed ?? 0) > 0 ? "warn" : undefined} />
          </div>
          <div className="mt-4 text-xs">
            {wl.outage_started_at ? (
              <div className="rounded-lg bg-bad/10 p-2 text-bad">
                Writes failing for {secs(new Date(st.server_time).getTime() / 1000 - wl.outage_started_at)} — {wl.last_error}
              </div>
            ) : (
              <div className="rounded-lg bg-ok/10 p-2 text-ok">Writes succeeding on {wl.last_acked_region} (txn #{wl.last_acked_seq})</div>
            )}
            {wl.last_outage && (
              <div className="mt-2 text-ink-400">
                Last customer-visible outage: <span className="text-ink-100">{secs(wl.last_outage.duration_s)}</span>, {wl.last_outage.failed_writes} failed writes,
                ended {time(new Date(wl.last_outage.ended_at * 1000).toISOString())}
              </div>
            )}
          </div>
        </Card>
      </div>

      <div className="grid gap-5 lg:grid-cols-5">
        <Card title={`Replication lag → ${standby}`} className="lg:col-span-3">
          <div className="h-52">
            {lagData.length ? (
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={lagData} margin={{ top: 8, right: 8, left: -18, bottom: 0 }}>
                  <defs>
                    <linearGradient id="lagFill" x1="0" x2="0" y1="0" y2="1">
                      <stop offset="0" stopColor="var(--color-azure)" stopOpacity={0.35} />
                      <stop offset="1" stopColor="var(--color-azure)" stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid stroke="var(--color-ink-800)" vertical={false} />
                  <XAxis dataKey="t" tick={{ fill: "var(--color-ink-400)", fontSize: 10 }} interval="preserveStartEnd" minTickGap={40} />
                  <YAxis tick={{ fill: "var(--color-ink-400)", fontSize: 10 }} unit="s" />
                  <Tooltip contentStyle={{ background: "var(--color-ink-850)", border: "1px solid var(--color-ink-700)", borderRadius: 8, fontSize: 12 }} formatter={(v) => [`${Number(v).toFixed(2)}s`, "lag"]} />
                  <ReferenceLine y={p.max_replication_lag_s} stroke="var(--color-warn)" strokeDasharray="4 4" label={{ value: "lag budget", fill: "var(--color-warn)", fontSize: 10, position: "insideTopRight" }} />
                  <Area type="monotone" dataKey="lag" stroke="var(--color-azure)" fill="url(#lagFill)" strokeWidth={2} isAnimationActive={false} connectNulls={false} />
                </AreaChart>
              </ResponsiveContainer>
            ) : (
              <Empty>Collecting samples…</Empty>
            )}
          </div>
        </Card>

        <Card title="Recommended action" className="lg:col-span-2" action={<Link to="/runs" className="text-xs text-azure hover:underline">Failover console →</Link>}>
          {plan ? (
            <>
              <div className="flex items-center gap-2">
                <Badge tone={plan.recommendation === "none" ? "ok" : plan.recommendation === "failover" ? "bad" : "warn"}>{plan.recommendation.replace(/_/g, " ")}</Badge>
                <span className="text-xs text-ink-400">{plan.scenario.replace(/_/g, " ")}</span>
              </div>
              <p className="mt-2 text-sm text-ink-300">{plan.rationale}</p>
              <ol className="mt-3 space-y-1 text-xs">
                {plan.steps.slice(0, 7).map((s) => (
                  <li key={s.order} className="flex gap-2">
                    <span className="font-mono text-ink-400">{s.order}.</span>
                    <span className="text-ink-100">{s.title}</span>
                    {!s.automated && <Badge className="ml-auto shrink-0">{s.owner}</Badge>}
                  </li>
                ))}
              </ol>
              {plan.estimated_rto_s !== null && (
                <div className="mt-3 text-xs text-ink-400">
                  Est. RTO <span className="font-mono text-ink-100">{secs(plan.estimated_rto_s)}</span> · est. RPO{" "}
                  <span className="font-mono text-ink-100">{secs(plan.estimated_rpo_s)}</span>
                </div>
              )}
            </>
          ) : (
            <Empty>Generating…</Empty>
          )}
        </Card>
      </div>

      <Card title="Open incidents" action={<Link to="/incidents" className="text-xs text-azure hover:underline">All incidents →</Link>}>
        {st.open_incidents.length === 0 ? (
          <Empty>No open incidents.</Empty>
        ) : (
          <ul className="divide-y divide-ink-800">
            {st.open_incidents.map((i) => (
              <li key={i.id} className="flex flex-wrap items-center gap-2 py-2 text-sm">
                <StatusBadge value={i.severity} />
                <StatusBadge value={i.status} />
                <span>{i.summary}</span>
                <span className="ml-auto text-xs text-ink-400">detected {time(i.detected_at)}</span>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}
