import clsx from "clsx";
import type { ButtonHTMLAttributes, ReactNode } from "react";

export function Card({ title, action, children, className }: { title?: ReactNode; action?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={clsx("rounded-xl border border-ink-700 bg-ink-900", className)}>
      {(title || action) && (
        <header className="flex items-center justify-between gap-3 border-b border-ink-800 px-4 py-2.5">
          <h2 className="text-[13px] font-semibold uppercase tracking-wider text-ink-300">{title}</h2>
          {action}
        </header>
      )}
      <div className="p-4">{children}</div>
    </section>
  );
}

const TONES = {
  ok: "bg-ok/10 text-ok ring-ok/30",
  warn: "bg-warn/10 text-warn ring-warn/30",
  bad: "bg-bad/10 text-bad ring-bad/30",
  info: "bg-azure/10 text-azure ring-azure/30",
  muted: "bg-ink-800 text-ink-300 ring-ink-600",
} as const;
export type Tone = keyof typeof TONES;

export function Badge({ tone = "muted", children, className }: { tone?: Tone; children: ReactNode; className?: string }) {
  return (
    <span className={clsx("inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[11px] font-medium uppercase tracking-wide ring-1 ring-inset", TONES[tone], className)}>
      {children}
    </span>
  );
}

export const statusTone = (s: string | null | undefined): Tone =>
  ({
    up: "ok", ready: "ok", completed: "ok", succeeded: "ok", verified: "ok", resolved: "ok", success: "ok", primary: "ok",
    degraded: "warn", at_risk: "warn", awaiting_approval: "warn", waiting: "warn", running: "info", replica: "info",
    mitigated: "warn", created: "muted", pending: "muted", skipped: "muted", cancelled: "muted", aborted: "muted",
    down: "bad", not_ready: "bad", failed: "bad", rejected: "bad", corrupt: "bad", open: "bad", failure: "bad",
    denied: "bad", fenced: "bad", critical: "bad", major: "warn", warning: "warn",
  } as Record<string, Tone>)[s ?? ""] ?? "muted";

export function StatusBadge({ value }: { value: string | null | undefined }) {
  return <Badge tone={statusTone(value)}>{(value ?? "unknown").replace(/_/g, " ")}</Badge>;
}

type BtnVariant = "primary" | "danger" | "ghost" | "default";
export function Button({ variant = "default", className, ...p }: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: BtnVariant }) {
  return (
    <button
      {...p}
      className={clsx(
        "inline-flex items-center justify-center gap-1.5 rounded-lg px-3 py-1.5 text-sm font-medium transition focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-azure disabled:cursor-not-allowed disabled:opacity-40",
        variant === "primary" && "bg-azure text-ink-950 hover:bg-azure/85",
        variant === "danger" && "bg-signal text-white hover:bg-signal/85",
        variant === "ghost" && "text-ink-300 hover:bg-ink-800 hover:text-ink-100",
        variant === "default" && "bg-ink-800 text-ink-100 ring-1 ring-inset ring-ink-600 hover:bg-ink-700",
        className,
      )}
    />
  );
}

export function Stat({ label, value, sub, tone }: { label: string; value: ReactNode; sub?: ReactNode; tone?: Tone }) {
  return (
    <div>
      <div className="text-[11px] uppercase tracking-wider text-ink-400">{label}</div>
      <div className={clsx("mt-0.5 font-mono text-xl font-semibold tabular", tone === "ok" && "text-ok", tone === "warn" && "text-warn", tone === "bad" && "text-bad")}>{value}</div>
      {sub && <div className="text-xs text-ink-400">{sub}</div>}
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="py-8 text-center text-sm text-ink-400">{children}</div>;
}

export function ErrorNote({ error }: { error: unknown }) {
  if (!error) return null;
  return <div role="alert" className="rounded-lg bg-bad/10 px-3 py-2 text-sm text-bad ring-1 ring-bad/30">{error instanceof Error ? error.message : String(error)}</div>;
}

export function CloudMark({ cloud, className }: { cloud: string; className?: string }) {
  return (
    <span className={clsx("inline-grid h-6 min-w-6 place-items-center rounded-md px-1 font-mono text-[10px] font-bold", cloud === "aws" ? "bg-aws/15 text-aws" : "bg-azure/15 text-azure", className)}>
      {cloud === "aws" ? "AWS" : "AZ"}
    </span>
  );
}

export function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="block text-sm">
      <span className="mb-1 block text-xs uppercase tracking-wider text-ink-400">{label}</span>
      {children}
    </label>
  );
}

export const inputCls =
  "w-full rounded-lg border border-ink-600 bg-ink-950 px-3 py-2 text-sm text-ink-100 placeholder:text-ink-400 focus:border-azure focus:outline-none";
