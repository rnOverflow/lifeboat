import clsx from "clsx";
import { Check, Clock, Download, Loader2, Minus, Pause, X } from "lucide-react";
import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Badge, Button, Card, Empty, ErrorNote, inputCls, Stat, StatusBadge } from "../components/ui";
import { api, type Run, type Step } from "../lib/api";
import { useAuth } from "../lib/auth";
import { secs, time } from "../lib/format";
import { useAction, useRun, useRuns, useStatus } from "../lib/queries";

const ICON = { succeeded: Check, failed: X, running: Loader2, waiting: Pause, pending: Clock, skipped: Minus } as const;

function Pipeline({ steps }: { steps: Step[] }) {
  return (
    <ol className="grid grid-cols-2 gap-2 sm:grid-cols-4 2xl:grid-cols-7">
      {steps.map((s, i) => {
        const Icon = ICON[s.status];
        const dur = s.started_at && s.finished_at ? (new Date(s.finished_at).getTime() - new Date(s.started_at).getTime()) / 1000 : null;
        return (
          <li
            key={s.name}
            className={clsx(
              "relative rounded-lg border p-2.5 text-xs",
              s.status === "succeeded" && "border-ok/40 bg-ok/5",
              s.status === "failed" && "border-bad/50 bg-bad/10",
              s.status === "waiting" && "border-warn/50 bg-warn/10",
              s.status === "running" && "border-azure/50 bg-azure/10",
              (s.status === "pending" || s.status === "skipped") && "border-ink-700 bg-ink-850 text-ink-400",
            )}
          >
            <div className="flex items-center gap-1.5">
              <span className="font-mono text-ink-400">{i + 1}</span>
              <Icon size={13} className={clsx(s.status === "running" && "animate-spin", s.status === "succeeded" && "text-ok", s.status === "failed" && "text-bad", s.status === "waiting" && "text-warn")} />
            </div>
            <div className="mt-1 font-medium leading-tight text-ink-100">{s.title}</div>
            <div className="mt-1 font-mono text-[10px] text-ink-400">{s.status === "waiting" ? "waiting" : dur !== null ? `${(dur * 1000).toFixed(0)} ms` : s.status}</div>
          </li>
        );
      })}
    </ol>
  );
}

function StepDetail({ s }: { s: Step }) {
  if (!Object.keys(s.output ?? {}).length) return null;
  return (
    <details className="rounded-lg bg-ink-850 px-3 py-2 text-xs">
      <summary className="cursor-pointer select-none text-ink-300">
        {s.title} <StatusBadge value={s.status} /> <span className="text-ink-400">· {s.attempts} attempt(s)</span>
      </summary>
      <pre className="mt-2 overflow-x-auto whitespace-pre-wrap break-all font-mono text-[11px] text-ink-300">{JSON.stringify(s.output, null, 2)}</pre>
    </details>
  );
}

function RunDetail({ run }: { run: Run }) {
  const { can, me } = useAuth();
  const { data: st } = useStatus();
  const [comment, setComment] = useState("");
  const approve = useAction(() => api(`/runs/${run.id}/approve`, { method: "POST", body: { comment } }));
  const reject = useAction(() => api(`/runs/${run.id}/reject`, { method: "POST", body: { comment: comment || "rejected" } }));
  const cancel = useAction(() => api(`/runs/${run.id}/cancel`, { method: "POST" }));
  const m = run.metrics ?? {};
  const awaiting = run.status === "awaiting_approval";
  const selfApproval = run.initiated_by === me?.username && !!st?.policy.two_person_rule;
  const download = async () => {
    const md = await api<string>(`/runs/${run.id}/report.md`, { raw: true });
    const url = URL.createObjectURL(new Blob([md], { type: "text/markdown" }));
    const a = Object.assign(document.createElement("a"), { href: url, download: `${run.kind}-${run.id}-report.md` });
    a.click();
    URL.revokeObjectURL(url);
  };
  return (
    <div className="space-y-4">
      <Card
        title={
          <span className="normal-case tracking-normal">
            <span className="capitalize">{run.kind}</span> #{run.id} · {run.source_region} → {run.target_region}
          </span>
        }
        action={<StatusBadge value={run.status} />}
      >
        <div className="mb-3 grid gap-x-6 gap-y-1 text-xs text-ink-400 sm:grid-cols-2">
          <div>Reason: <span className="text-ink-100">{run.reason}</span></div>
          <div>Initiated by <span className="text-ink-100">{run.initiated_by}</span> at {time(run.created_at)}</div>
          <div>Approved by <span className="text-ink-100">{run.approved_by ?? "—"}</span>{run.approved_at && ` at ${time(run.approved_at)}`}</div>
          {run.incident_id && <div>Incident #{run.incident_id}</div>}
        </div>
        <Pipeline steps={run.steps} />
        {run.error && <div className="mt-3"><ErrorNote error={run.error} /></div>}
        {awaiting && (
          <div className="mt-4 rounded-lg border border-warn/40 bg-warn/5 p-3">
            <div className="mb-2 text-sm font-medium">Approval required</div>
            {can("approve_run") ? (
              <>
                <input className={inputCls} placeholder="Comment (recorded in the audit log)" value={comment} onChange={(e) => setComment(e.target.value)} />
                <div className="mt-2 flex flex-wrap gap-2">
                  <Button variant="primary" onClick={() => approve.mutate()} disabled={approve.isPending || selfApproval}>Approve {run.kind}</Button>
                  <Button variant="danger" onClick={() => reject.mutate()} disabled={reject.isPending}>Reject</Button>
                  {selfApproval && <span className="self-center text-xs text-warn">Two-person rule: you initiated this run and cannot approve it.</span>}
                </div>
              </>
            ) : (
              <div className="text-sm text-ink-400">Waiting for a user with the approver role.</div>
            )}
            <ErrorNote error={approve.error || reject.error} />
          </div>
        )}
        {["pending", "awaiting_approval"].includes(run.status) && can("initiate_run") && (
          <Button variant="ghost" className="mt-2" onClick={() => cancel.mutate()}>Cancel run</Button>
        )}
      </Card>

      {(run.status === "completed" || run.status === "failed") && (
        <Card title="Measured objectives" action={run.report && <Button variant="ghost" onClick={download}><Download size={14} /> Report (.md)</Button>}>
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
            <Stat label="RTO measured" value={secs(m.rto_s, 2)} tone={m.rto_met ? "ok" : m.rto_met === false ? "bad" : undefined} sub={`target ${secs(m.rto_target_s, 0)}`} />
            <Stat label="RPO measured" value={secs(m.rpo_s, 2)} tone={m.rpo_met ? "ok" : m.rpo_met === false ? "bad" : undefined} sub={`target ${secs(m.rpo_target_s, 0)}`} />
            <Stat label="Lost transactions" value={m.data_loss_txns ?? "—"} tone={m.data_loss_txns ? "warn" : "ok"} sub={m.planned ? "planned switchover" : "unplanned"} />
            {run.kind === "failback" ? (
              <Stat label="Divergent writes discarded" value={m.divergent_discarded ?? 0} sub="during resync" />
            ) : (
              <Stat label="Customer outage" value={secs(run.report?.customer_impact?.duration_s)} sub={`${run.report?.customer_impact?.failed_writes ?? 0} failed writes`} />
            )}
          </div>
          {m.rto_basis && <div className="mt-2 text-xs text-ink-400">RTO basis: {m.rto_basis}</div>}
          <div className="mt-4">
            <div className="mb-1 text-xs uppercase tracking-wider text-ink-400">Phase breakdown</div>
            <PhaseBar detect={m.time_to_detect_s} approve={m.time_to_approve_s} execute={m.time_to_execute_s} />
          </div>
          {run.report && (
            <div className="mt-4">
              <div className="mb-1 text-xs uppercase tracking-wider text-ink-400">Recommendations</div>
              <ul className="space-y-1 text-sm text-ink-300">{run.report.recommendations.map((r) => <li key={r}>• {r}</li>)}</ul>
            </div>
          )}
        </Card>
      )}

      {run.plan && run.plan.steps?.length > 0 && (
        <Card title="Plan at initiation">
          <p className="text-sm text-ink-300">{run.plan.rationale}</p>
          {run.plan.risks.length > 0 && <ul className="mt-2 text-xs text-warn">{run.plan.risks.map((r) => <li key={r}>⚠ {r}</li>)}</ul>}
        </Card>
      )}
      <div className="space-y-2">{run.steps.map((s) => <StepDetail key={s.name} s={s} />)}</div>
    </div>
  );
}

function PhaseBar({ detect, approve, execute }: { detect?: number | null; approve?: number | null; execute?: number | null }) {
  const parts = [
    { k: "Detect", v: detect ?? 0, c: "bg-bad" },
    { k: "Decide", v: approve ?? 0, c: "bg-warn" },
    { k: "Execute", v: execute ?? 0, c: "bg-ok" },
  ];
  const total = parts.reduce((a, b) => a + b.v, 0) || 1;
  return (
    <div>
      <div className="flex h-3 overflow-hidden rounded bg-ink-800">
        {parts.map((p) => <div key={p.k} className={p.c} style={{ width: `${(p.v / total) * 100}%` }} title={`${p.k}: ${secs(p.v, 2)}`} />)}
      </div>
      <div className="mt-1 flex flex-wrap gap-4 text-xs text-ink-400">
        {parts.map((p) => (
          <span key={p.k} className="flex items-center gap-1"><span className={clsx("h-2 w-2 rounded-sm", p.c)} /> {p.k} <span className="font-mono text-ink-100">{secs(p.v, 2)}</span></span>
        ))}
      </div>
    </div>
  );
}

export default function Runs() {
  const [params, setParams] = useSearchParams();
  const { data: runs } = useRuns();
  const { data: st } = useStatus();
  const { can } = useAuth();
  const selected = Number(params.get("id")) || runs?.[0]?.id || null;
  const { data: run } = useRun(selected);
  const [reason, setReason] = useState("");
  const start = useAction((kind: "failover" | "failback") => api<Run>(`/runs/${kind}`, { method: "POST", body: { reason } }));

  return (
    <div className="mx-auto grid max-w-7xl gap-5 lg:grid-cols-[20rem_1fr]">
      <div className="space-y-4">
        {can("initiate_run") && (
          <Card title="Start a run">
            <input className={inputCls} placeholder="Reason (min 3 chars)" value={reason} onChange={(e) => setReason(e.target.value)} />
            <div className="mt-2 flex gap-2">
              <Button variant="danger" disabled={reason.length < 3 || !!st?.active_run || start.isPending}
                onClick={() => start.mutate("failover", { onSuccess: (r) => { setParams({ id: String(r.id) }); setReason(""); } })}>
                Failover
              </Button>
              <Button disabled={reason.length < 3 || !st?.failed_over || !!st?.active_run || start.isPending}
                onClick={() => start.mutate("failback", { onSuccess: (r) => { setParams({ id: String(r.id) }); setReason(""); } })}>
                Failback
              </Button>
            </div>
            <p className="mt-2 text-xs text-ink-400">
              Failover from a healthy region runs as a planned switchover (drain first, zero data loss). Every run stops at the approval gate.
            </p>
            <div className="mt-2"><ErrorNote error={start.error} /></div>
          </Card>
        )}
        <Card title="Runs">
          {!runs?.length ? <Empty>No runs yet.</Empty> : (
            <ul className="-mx-2 space-y-1">
              {runs.map((r) => (
                <li key={r.id}>
                  <button onClick={() => setParams({ id: String(r.id) })}
                    className={clsx("w-full rounded-lg px-2 py-2 text-left text-sm hover:bg-ink-850", r.id === selected && "bg-ink-800")}>
                    <div className="flex items-center gap-2">
                      <span className="font-mono text-ink-400">#{r.id}</span>
                      <span className="capitalize">{r.kind}</span>
                      <span className="ml-auto"><StatusBadge value={r.status} /></span>
                    </div>
                    <div className="mt-0.5 flex gap-3 text-xs text-ink-400">
                      <span>{time(r.created_at)}</span>
                      {r.metrics?.rto_s != null && <span>RTO <span className="font-mono text-ink-100">{secs(r.metrics.rto_s)}</span></span>}
                      {r.game_day_id && <Badge>game day</Badge>}
                    </div>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
      <div>{run ? <RunDetail run={run} /> : <Card><Empty>Select a run, or start one when a failure is detected.</Empty></Card>}</div>
    </div>
  );
}
