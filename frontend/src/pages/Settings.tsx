import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Button, Card, ErrorNote, Field, inputCls, StatusBadge } from "../components/ui";
import { api, type Policy } from "../lib/api";
import { useAuth } from "../lib/auth";
import { useAction } from "../lib/queries";

type PolicyDoc = Policy & { updated_by: string; updated_at: string };

function PolicyForm() {
  const { data } = useQuery({ queryKey: ["policy"], queryFn: () => api<PolicyDoc>("/policy") });
  return data ? <PolicyEditor key={data.updated_at} data={data} /> : null;
}

function PolicyEditor({ data }: { data: PolicyDoc }) {
  const { can } = useAuth();
  const [p, setP] = useState<Policy>(data);
  const save = useAction(() => api("/policy", { method: "PUT", body: p }));
  const editable = can("manage_policy");
  const num = (k: keyof Policy, l: string) => (
    <Field label={l}><input type="number" className={inputCls} disabled={!editable} value={p[k] as number} onChange={(e) => setP({ ...p, [k]: Number(e.target.value) })} /></Field>
  );
  const bool = (k: keyof Policy, l: string) => (
    <label className="flex items-center gap-2 text-sm"><input type="checkbox" disabled={!editable} checked={p[k] as boolean} onChange={(e) => setP({ ...p, [k]: e.target.checked })} /> {l}</label>
  );
  return (
    <Card title="DR policy" action={<span className="text-xs text-ink-400">last changed by {data.updated_by}</span>}>
      <div className="grid gap-3 sm:grid-cols-2">
        {num("rto_target_s", "RTO target (s)")}
        {num("rpo_target_s", "RPO target (s)")}
        {num("max_replication_lag_s", "Replication lag budget (s)")}
        {num("backup_max_age_s", "Max verified backup age (s)")}
      </div>
      <div className="mt-4 space-y-2">
        {bool("auto_initiate_failover", "Auto-initiate failover when the active region is declared down")}
        {bool("require_approval", "Require human approval before promotion")}
        {bool("two_person_rule", "Two-person rule (initiator cannot approve)")}
      </div>
      {editable ? <Button variant="primary" className="mt-4" onClick={() => save.mutate()} disabled={save.isPending}>{save.isSuccess ? "Saved" : "Save policy"}</Button>
        : <p className="mt-4 text-xs text-ink-400">Only admins can change the policy.</p>}
      <div className="mt-2"><ErrorNote error={save.error} /></div>
    </Card>
  );
}

interface U { id: number; username: string; role: string; is_active: boolean }
function Users() {
  const { data } = useQuery({ queryKey: ["users"], queryFn: () => api<U[]>("/users") });
  const [form, setForm] = useState({ username: "", password: "", role: "viewer" });
  const create = useAction(() => api("/users", { method: "POST", body: form }));
  const patch = useAction((v: { id: number; body: Partial<U> }) => api(`/users/${v.id}`, { method: "PATCH", body: v.body }));
  return (
    <Card title="Users & roles">
      <table className="w-full text-sm">
        <tbody className="divide-y divide-ink-800">
          {data?.map((u) => (
            <tr key={u.id}>
              <td className="py-2">{u.username}</td>
              <td>
                <select className="rounded border border-ink-600 bg-ink-950 px-2 py-1 text-sm" value={u.role} onChange={(e) => patch.mutate({ id: u.id, body: { role: e.target.value } })}>
                  {["viewer", "operator", "approver", "admin"].map((r) => <option key={r}>{r}</option>)}
                </select>
              </td>
              <td><StatusBadge value={u.is_active ? "active" : "disabled"} /></td>
              <td className="text-right"><Button variant="ghost" onClick={() => patch.mutate({ id: u.id, body: { is_active: !u.is_active } })}>{u.is_active ? "Disable" : "Enable"}</Button></td>
            </tr>
          ))}
        </tbody>
      </table>
      <form className="mt-4 grid gap-2 sm:grid-cols-[1fr_1fr_8rem_auto]" onSubmit={(e) => { e.preventDefault(); create.mutate(undefined, { onSuccess: () => setForm({ username: "", password: "", role: "viewer" }) }); }}>
        <input className={inputCls} placeholder="username" value={form.username} onChange={(e) => setForm({ ...form, username: e.target.value })} />
        <input className={inputCls} type="password" placeholder="password (12+ chars)" autoComplete="new-password" value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} />
        <select className={inputCls} value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })}>{["viewer", "operator", "approver", "admin"].map((r) => <option key={r}>{r}</option>)}</select>
        <Button type="submit" variant="primary">Add</Button>
      </form>
      <div className="mt-2"><ErrorNote error={create.error || patch.error} /></div>
    </Card>
  );
}

export default function Settings() {
  const { can } = useAuth();
  return (
    <div className="mx-auto grid max-w-6xl gap-5 lg:grid-cols-2">
      <PolicyForm />
      {can("manage_users") && <Users />}
    </div>
  );
}
