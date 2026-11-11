import clsx from "clsx";
import { useState } from "react";
import { Link } from "react-router-dom";
import { Badge, Button, Card, Empty, ErrorNote, Field, inputCls, Stat, StatusBadge } from "../components/ui";
import { api, type FaultType, type GameDay } from "../lib/api";
import { useAuth } from "../lib/auth";
import { label, secs, time } from "../lib/format";
import { useAction, useGameDays, useStatus } from "../lib/queries";

const SCENARIOS: FaultType[] = ["cloud_outage", "database_failure", "network_failure", "service_failure", "replication_failure"];

function Report({ g }: { g: GameDay }) {
  const r = g.report;
  if (!r) return null;
  return (
    <div className="mt-4 space-y-3">
      <div className="flex items-center gap-2">
        <Badge tone={r.passed ? "ok" : "bad"}>{r.passed ? "passed" : "failed"}</Badge>
        <span className="text-sm text-ink-400">Recovery report</span>
      </div>
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
        <Stat label="Detection" value={secs(r.detection_s)} sub="fault → incident" />
        <Stat label="Failover RTO" value={secs(r.failover?.rto_s, 2)} tone={r.failover?.rto_met ? "ok" : r.failover?.rto_met === false ? "bad" : undefined} />
        <Stat label="Failover RPO" value={secs(r.failover?.rpo_s, 2)} tone={r.failover?.rpo_met ? "ok" : r.failover?.rpo_met === false ? "bad" : undefined}
          sub={r.failover ? `${r.failover.data_loss_txns ?? 0} txns lost` : "no failover"} />
        <Stat label="Readiness" value={`${r.readiness_before ?? "—"} → ${r.readiness_after}`} />
      </div>
      <ul className="space-y-1 text-sm text-ink-300">{r.findings.map((f) => <li key={f}>• {f}</li>)}</ul>
      <div className="flex gap-3 text-xs">
        {r.failover && <Link className="text-azure hover:underline" to={`/runs?id=${r.failover.id}`}>Failover run #{r.failover.id} →</Link>}
        {r.failback && <Link className="text-azure hover:underline" to={`/runs?id=${r.failback.id}`}>Failback run #{r.failback.id} →</Link>}
      </div>
    </div>
  );
}

export default function GameDays() {
  const { data: gds } = useGameDays();
  const { data: st } = useStatus();
  const { can, me } = useAuth();
  const [name, setName] = useState("Quarterly region evacuation drill");
  const [scenario, setScenario] = useState<FaultType>("cloud_outage");
  const [region, setRegion] = useState("");
  const [autoApprove, setAutoApprove] = useState(false);
  const [failback, setFailback] = useState(true);
  const [sel, setSel] = useState<number | null>(null);
  const start = useAction(() =>
    api<GameDay>("/gamedays", { method: "POST", body: { name, scenario, target_region: region || null, auto_approve: autoApprove, include_failback: failback } }),
  );
  const abort = useAction((id: number) => api(`/gamedays/${id}/abort`, { method: "POST" }));
  const canPreapprove = me?.role === "approver" || me?.role === "admin";
  const current = gds?.find((g) => g.id === (sel ?? gds?.[0]?.id));

  return (
    <div className="mx-auto grid max-w-7xl gap-5 lg:grid-cols-[22rem_1fr]">
      <div className="space-y-4">
        <Card title="Run a game day">
          <div className="space-y-3">
            <Field label="Name"><input className={inputCls} value={name} onChange={(e) => setName(e.target.value)} /></Field>
            <Field label="Scenario">
              <select className={inputCls} value={scenario} onChange={(e) => setScenario(e.target.value as FaultType)}>
                {SCENARIOS.map((s) => <option key={s} value={s}>{label(s)}</option>)}
              </select>
            </Field>
            <Field label="Target region">
              <select className={inputCls} value={region} onChange={(e) => setRegion(e.target.value)}>
                <option value="">Active region ({st?.active_region})</option>
                {st?.regions.map((r) => <option key={r.name} value={r.name}>{r.display_name}</option>)}
              </select>
            </Field>
            <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={failback} onChange={(e) => setFailback(e.target.checked)} /> Fail back after recovery</label>
            <label className={clsx("flex items-center gap-2 text-sm", !canPreapprove && "opacity-50")}>
              <input type="checkbox" disabled={!canPreapprove} checked={autoApprove} onChange={(e) => setAutoApprove(e.target.checked)} /> Pre-approve recovery runs
            </label>
            {!canPreapprove && <p className="text-xs text-ink-400">Without pre-approval, an approver must approve each run as it reaches the gate.</p>}
            <Button variant="danger" className="w-full" disabled={!can("gameday") || start.isPending || name.length < 3}
              onClick={() => start.mutate(undefined, { onSuccess: (g) => setSel(g.id) })}>Start game day</Button>
            <ErrorNote error={start.error} />
          </div>
        </Card>
        <Card title="History">
          {!gds?.length ? <Empty>No game days yet.</Empty> : (
            <ul className="-mx-2 space-y-1">
              {gds.map((g) => (
                <li key={g.id}>
                  <button onClick={() => setSel(g.id)} className={clsx("w-full rounded-lg px-2 py-2 text-left text-sm hover:bg-ink-850", current?.id === g.id && "bg-ink-800")}>
                    <div className="flex items-center gap-2"><span className="truncate">{g.name}</span><span className="ml-auto"><StatusBadge value={g.status} /></span></div>
                    <div className="text-xs capitalize text-ink-400">{label(g.scenario)} · {g.target_region} · {time(g.started_at)}</div>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
      <div>
        {current ? (
          <Card title={<span className="normal-case tracking-normal">{current.name}</span>}
            action={<div className="flex items-center gap-2"><Badge>{current.phase}</Badge><StatusBadge value={current.status} />
              {current.status === "running" && <Button variant="ghost" onClick={() => abort.mutate(current.id)}>Abort</Button>}</div>}>
            <ol className="relative space-y-3 border-l border-ink-700 pl-5">
              {current.timeline.map((e, i) => (
                <li key={i} className="text-sm">
                  <span className={clsx("absolute -left-[5px] mt-1.5 h-2.5 w-2.5 rounded-full", e.phase === "done" ? "bg-ok" : "bg-azure")} />
                  <div className="flex flex-wrap gap-x-2"><span className="font-mono text-xs text-ink-400">{time(e.at)}</span><Badge>{e.phase}</Badge></div>
                  <div className="text-ink-300">{e.detail}</div>
                </li>
              ))}
            </ol>
            {current.status === "running" && current.failover_run_id && (
              <div className="mt-3 text-sm"><Link className="text-azure hover:underline" to={`/runs?id=${current.failback_run_id ?? current.failover_run_id}`}>Open the current run →</Link></div>
            )}
            <Report g={current} />
          </Card>
        ) : <Card><Empty>Start a game day to rehearse a failure end-to-end.</Empty></Card>}
      </div>
    </div>
  );
}
