export const secs = (v: number | null | undefined, digits = 1) =>
  v === null || v === undefined ? "—" : v < 60 ? `${v.toFixed(digits)}s` : `${Math.floor(v / 60)}m ${Math.round(v % 60)}s`;

export const ago = (iso: string | null | undefined, now = Date.now()) => {
  if (!iso) return "—";
  const d = (now - new Date(iso).getTime()) / 1000;
  if (d < 5) return "just now";
  if (d < 60) return `${Math.round(d)}s ago`;
  if (d < 3600) return `${Math.round(d / 60)}m ago`;
  if (d < 86400) return `${Math.round(d / 3600)}h ago`;
  return `${Math.round(d / 86400)}d ago`;
};

export const time = (iso: string | null | undefined) => (iso ? new Date(iso).toLocaleTimeString() : "—");
export const bytes = (n: number) => (n < 1024 ? `${n} B` : n < 1048576 ? `${(n / 1024).toFixed(1)} KB` : `${(n / 1048576).toFixed(1)} MB`);
export const label = (s: string) => s.replace(/_/g, " ");
