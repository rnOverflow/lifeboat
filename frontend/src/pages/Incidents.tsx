import { Card, Empty, StatusBadge } from "../components/ui";
import { label, secs, time } from "../lib/format";
import { useIncidents } from "../lib/queries";

export default function Incidents() {
  const { data } = useIncidents();
  return (
    <div className="mx-auto max-w-7xl">
      <Card title="Incidents">
        {!data?.length ? <Empty>No incidents recorded.</Empty> : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-left text-xs uppercase tracking-wider text-ink-400">
                <tr><th className="py-2 pr-3">#</th><th className="pr-3">Severity</th><th className="pr-3">Status</th><th className="pr-3">Kind</th><th className="pr-3">Summary</th><th className="pr-3">Detected</th><th className="pr-3">Time to detect</th><th>Duration</th></tr>
              </thead>
              <tbody className="divide-y divide-ink-800">
                {data.map((i) => {
                  const ttd = (new Date(i.detected_at).getTime() - new Date(i.started_at).getTime()) / 1000;
                  const dur = i.resolved_at ? (new Date(i.resolved_at).getTime() - new Date(i.started_at).getTime()) / 1000 : null;
                  return (
                    <tr key={i.id}>
                      <td className="py-2 pr-3 font-mono text-ink-400">{i.id}</td>
                      <td className="pr-3"><StatusBadge value={i.severity} /></td>
                      <td className="pr-3"><StatusBadge value={i.status} /></td>
                      <td className="pr-3 capitalize">{label(i.kind)}</td>
                      <td className="pr-3">{i.summary}
                        {"auto_remediation" in i.details && <div className="text-xs text-ok">self-heal: service restarted automatically</div>}
                        {"failover_run_id" in i.details && <div className="text-xs text-ink-400">failover run #{String(i.details.failover_run_id)}</div>}
                      </td>
                      <td className="pr-3 text-ink-400">{time(i.detected_at)}</td>
                      <td className="pr-3 font-mono tabular">{secs(ttd)}</td>
                      <td className="font-mono tabular">{dur === null ? "ongoing" : secs(dur)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}
