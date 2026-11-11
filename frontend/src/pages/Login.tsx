import { useState } from "react";
import { Button, ErrorNote, Field, inputCls } from "../components/ui";
import { useAuth } from "../lib/auth";

export default function Login() {
  const { login } = useAuth();
  const [u, setU] = useState("");
  const [p, setP] = useState("");
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  return (
    <div className="grid min-h-full place-items-center px-4">
      <form
        className="w-full max-w-sm rounded-2xl border border-ink-700 bg-ink-900 p-6"
        onSubmit={async (e) => {
          e.preventDefault();
          setBusy(true);
          setErr(null);
          try { await login(u, p); } catch (x) { setErr(x); } finally { setBusy(false); }
        }}
      >
        <div className="mb-5 flex items-center gap-2">
          <svg viewBox="0 0 32 32" className="h-8 w-8" aria-hidden>
            <circle cx="16" cy="16" r="12" fill="none" stroke="var(--color-signal)" strokeWidth="5" />
            <circle cx="16" cy="16" r="12" fill="none" stroke="white" strokeWidth="5" strokeDasharray="9.42 9.42" />
          </svg>
          <div>
            <h1 className="text-lg font-bold leading-tight">Lifeboat</h1>
            <p className="text-xs text-ink-400">Multi-cloud disaster recovery</p>
          </div>
        </div>
        <div className="space-y-3">
          <Field label="Username"><input className={inputCls} autoComplete="username" value={u} onChange={(e) => setU(e.target.value)} required /></Field>
          <Field label="Password"><input className={inputCls} type="password" autoComplete="current-password" value={p} onChange={(e) => setP(e.target.value)} required /></Field>
          <ErrorNote error={err} />
          <Button variant="primary" type="submit" className="w-full" disabled={busy}>{busy ? "Signing in…" : "Sign in"}</Button>
        </div>
      </form>
    </div>
  );
}
