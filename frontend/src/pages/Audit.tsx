import { useQuery } from "@tanstack/react-query";
import { ShieldCheck, ShieldX } from "lucide-react";
import { useState } from "react";
import { Card, Empty, inputCls, StatusBadge } from "../components/ui";
import { api, type AuditEntry } from "../lib/api";
import { time } from "../lib/format";

export default function Audit() {
  const [action, setAction] = useState("");
  const [actor, setActor] = useState("");
  const q = new URLSearchParams({ limit: "300", ...(action && { action }), ...(actor && { actor }) });
  const { data } = useQuery({ queryKey: ["audit", action, actor], queryFn: () => api<AuditEntry[]>(`/audit?${q}`), refetchInterval: 3000 });
  const { data: chain } = useQuery({ queryKey: ["audit-verify"], queryFn: () => api<{ valid: boolean; entries_checked: number; broken_at_id?: number }>("/audit/verify"), refetchInterval: 10000 });
  return (
    <div className="mx-auto max-w-7xl space-y-4">
      {chain && (
        <div className={`flex items-center gap-2 rounded-xl px-4 py-3 text-sm ring-1 ${chain.valid ? "bg-ok/10 text-ok ring-ok/30" : "bg-bad/10 text-bad ring-bad/30"}`}>
          {chain.valid ? <ShieldCheck size={18} /> : <ShieldX size={18} />}
          {chain.valid ? `Hash chain intact: ${chain.entries_checked} entries verified (SHA-256, append-only).` : `TAMPERING DETECTED at entry #${chain.broken_at_id}`}
        </div>
      )}
      <Card title="Audit log" action={
        <div className="flex gap-2">
          <input className={inputCls + " w-40"} placeholder="action prefix" value={action} onChange={(e) => setAction(e.target.value)} />
          <input className={inputCls + " w-32"} placeholder="actor" value={actor} onChange={(e) => setActor(e.target.value)} />
        </div>
      }>
        {!data?.length ? <Empty>No entries.</Empty> : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-left text-xs uppercase tracking-wider text-ink-400">
                <tr><th className="py-2 pr-3">#</th><th className="pr-3">Time</th><th className="pr-3">Actor</th><th className="pr-3">Action</th><th className="pr-3">Target</th><th className="pr-3">Outcome</th><th className="pr-3">Details</th><th>Hash</th></tr>
              </thead>
              <tbody className="divide-y divide-ink-800">
                {data.map((e) => (
                  <tr key={e.id} className="align-top">
                    <td className="py-1.5 pr-3 font-mono text-ink-400">{e.id}</td>
                    <td className="whitespace-nowrap pr-3 text-ink-400">{time(e.ts)}</td>
                    <td className="pr-3">{e.actor}</td>
                    <td className="pr-3 font-mono text-xs">{e.action}</td>
                    <td className="pr-3 text-ink-300">{e.target ?? "—"}</td>
                    <td className="pr-3"><StatusBadge value={e.outcome} /></td>
                    <td className="max-w-xs truncate pr-3 font-mono text-[11px] text-ink-400" title={JSON.stringify(e.details)}>{JSON.stringify(e.details)}</td>
                    <td className="font-mono text-[11px] text-ink-400">{e.hash.slice(0, 10)}</td>
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
