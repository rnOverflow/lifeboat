export default function Gauge({ value, grade, tone }: { value: number; grade: string; tone: "ok" | "warn" | "bad" }) {
  const r = 52;
  const c = Math.PI * r;
  const color = tone === "ok" ? "var(--color-ok)" : tone === "warn" ? "var(--color-warn)" : "var(--color-bad)";
  return (
    <svg viewBox="0 0 128 76" className="w-40" role="img" aria-label={`Readiness ${value} of 100, grade ${grade}`}>
      <path d="M12 66 A52 52 0 0 1 116 66" fill="none" stroke="var(--color-ink-700)" strokeWidth="10" strokeLinecap="round" />
      <path d="M12 66 A52 52 0 0 1 116 66" fill="none" stroke={color} strokeWidth="10" strokeLinecap="round" strokeDasharray={`${(c * value) / 100} ${c}`} style={{ transition: "stroke-dasharray .6s" }} />
      <text x="64" y="58" textAnchor="middle" className="fill-ink-100 font-mono text-[26px] font-bold">{Math.round(value)}</text>
      <text x="64" y="73" textAnchor="middle" className="fill-ink-400 text-[9px] uppercase tracking-widest">grade {grade}</text>
    </svg>
  );
}
