import { ShieldCheck } from "lucide-react";
import { useState } from "react";
import { Button, Card, Empty, ErrorNote, StatusBadge } from "../components/ui";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";
import { ago, bytes } from "../lib/format";
import { useAction, useBackups, useStatus } from "../lib/queries";

export default function Backups() {
  const { data: backups } = useBackups();
  const { data: st } = useStatus();
  const { can } = useAuth();
  const [region, setRegion] = useState("");
  const create = useAction(() => api("/backups", { method: "POST", body: { region: region || null } }));
  const verify = useAction((id: number) => api(`/backups/${id}/verify`, { method: "POST" }));
  const corrupt = useAction((id: number) => api(`/simulator/backups/${id}/corrupt`, { method: "POST" }));
  return (
    <div className="mx-auto max-w-7xl space-y-5">
      <Card title="Backups" action={
        can("backup") && (
          <div className="flex items-center gap-2">
            <select className="rounded-lg border border-ink-600 bg-ink-950 px-2 py-1 text-sm" value={region} onChange={(e) => setRegion(e.target.value)}>
              <option value="">Active region</option>
              {st?.regions.map((r) => <option key={r.name} value={r.name}>{r.display_name}</option>)}
            </select>
            <Button variant="primary" onClick={() => create.mutate()} disabled={create.isPending}>Take backup</Button>
          </div>
        )
      }>
        <p className="mb-3 text-sm text-ink-400">
          Verification recomputes the SHA-256 of the stored object and runs a test restore into an isolated scratch structure. It checks sequence integrity and record count against the catalogue.
        </p>
        <ErrorNote error={create.error || verify.error || corrupt.error} />
        {!backups?.length ? <Empty>No backups yet.</Empty> : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-left text-xs uppercase tracking-wider text-ink-400">
                <tr><th className="py-2 pr-3">#</th><th className="pr-3">Region</th><th className="pr-3">Taken</th><th className="pr-3">Records</th><th className="pr-3">Size</th><th className="pr-3">SHA-256</th><th className="pr-3">Integrity</th><th /></tr>
              </thead>
              <tbody className="divide-y divide-ink-800">
                {backups.map((b) => (
                  <tr key={b.id} className="align-top">
                    <td className="py-2 pr-3 font-mono text-ink-400">{b.id}</td>
                    <td className="pr-3">{b.region}</td>
                    <td className="pr-3 text-ink-400">{ago(b.created_at)}<div className="text-xs">by {b.created_by}</div></td>
                    <td className="pr-3 font-mono tabular">{b.record_count}<div className="text-xs text-ink-400">≤ txn #{b.max_seq}</div></td>
                    <td className="pr-3 font-mono tabular">{bytes(b.size_bytes)}</td>
                    <td className="pr-3 font-mono text-xs text-ink-400" title={b.checksum_sha256}>{b.checksum_sha256.slice(0, 12)}…</td>
                    <td className="pr-3">
                      <StatusBadge value={b.status} />
                      {b.verification?.issues?.length ? <ul className="mt-1 text-xs text-bad">{b.verification.issues.map((i) => <li key={i}>{i}</li>)}</ul> : null}
                      {b.verified_at && <div className="text-xs text-ink-400">checked {ago(b.verified_at)}{b.verification.duration_ms !== undefined && ` · ${b.verification.duration_ms} ms`}</div>}
                    </td>
                    <td className="space-x-1 whitespace-nowrap text-right">
                      {can("backup") && <Button variant="ghost" onClick={() => verify.mutate(b.id)}><ShieldCheck size={14} /> Verify</Button>}
                      {can("inject_fault") && st?.mode === "local" && <Button variant="ghost" className="text-bad" onClick={() => corrupt.mutate(b.id)} title="Simulate bit-rot in the stored object">Corrupt</Button>}
                    </td>
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
