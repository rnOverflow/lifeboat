// Typed client for the Lifeboat API. The JWT lives in sessionStorage (cleared when the tab closes).
const TOKEN_KEY = "lifeboat.token";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

export function getToken(): string | null {
  try {
    return sessionStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function setToken(t: string | null) {
  try {
    if (t) sessionStorage.setItem(TOKEN_KEY, t);
    else sessionStorage.removeItem(TOKEN_KEY);
  } catch {
    /* storage unavailable */
  }
}

export async function api<T = unknown>(path: string, init: { method?: string; body?: unknown; raw?: boolean } = {}): Promise<T> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  const tok = getToken();
  if (tok) headers.Authorization = `Bearer ${tok}`;
  const res = await fetch(`/api/v1${path}`, {
    method: init.method ?? "GET",
    headers,
    body: init.body === undefined ? undefined : JSON.stringify(init.body),
  });
  if (res.status === 401 && tok) {
    setToken(null);
    window.dispatchEvent(new Event("lifeboat:logout"));
  }
  if (!res.ok) {
    let msg = res.statusText;
    try {
      const j = await res.json();
      msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail);
    } catch {
      /* not json */
    }
    throw new ApiError(res.status, msg);
  }
  return (init.raw ? res.text() : res.json()) as Promise<T>;
}

export type RegionStatus = "up" | "degraded" | "down";
export type FaultType = "cloud_outage" | "database_failure" | "network_failure" | "service_failure" | "replication_failure";

export interface Component { ok: boolean; latency_ms: number; detail: string }
export interface Region {
  name: string; cloud: "aws" | "azure"; display_name: string; designated_role: string; status: RegionStatus;
  db_role: string | null; replication_lag_s: number | null; last_applied_seq: number | null;
  consecutive_failures: number; last_probe_at: string | null; first_failure_at: string | null;
  probe: { healthy?: boolean; latency_ms?: number; app?: Component; database?: Component; network?: Component; failing?: string[] };
}
export interface ReadinessComponent { key: string; label: string; weight: number; score: number; detail: string }
export interface Readiness {
  score: number; grade: string; status: "ready" | "at_risk" | "not_ready"; components: ReadinessComponent[];
  blockers: string[]; protected_region: string; standby_region: string;
}
export interface Policy {
  rto_target_s: number; rpo_target_s: number; max_replication_lag_s: number; backup_max_age_s: number;
  auto_initiate_failover: boolean; require_approval: boolean; two_person_rule: boolean;
}
export interface IncidentLite { id: number; region: string; kind: string; severity: string; status: string; summary: string; detected_at: string }
export interface Workload {
  ok?: number; failed?: number; last_error?: string | null; last_acked_region?: string; last_acked_seq?: number;
  outage_started_at?: number | null; last_outage?: { duration_s: number; failed_writes: number; ended_at: number } | null;
}
export interface Status {
  mode: string; active_region: string; standby_region: string; designated_primary: string; failed_over: boolean;
  regions: Region[]; rpo_exposure_s: number | null; policy: Policy; readiness: Readiness; workload: Workload;
  open_incidents: IncidentLite[]; active_run: { id: number; kind: string; status: string } | null;
  latest_backup: { id: number; status: string; age_s: number } | null; measured_rto_s: number[];
  engine: { last_tick_at: string | null; tick_errors: number }; server_time: string;
}
export interface Step {
  position: number; name: string; title: string; status: "pending" | "running" | "succeeded" | "failed" | "skipped" | "waiting";
  attempts: number; started_at: string | null; finished_at: string | null; output: Record<string, unknown>;
}
export interface RunMetrics {
  rto_s?: number | null; rpo_s?: number | null; rto_met?: boolean | null; rpo_met?: boolean | null; rto_target_s?: number;
  rpo_target_s?: number; data_loss_txns?: number; planned?: boolean; time_to_detect_s?: number | null;
  time_to_approve_s?: number | null; time_to_execute_s?: number | null; divergent_discarded?: number; rto_basis?: string;
}
export interface RunReport {
  title: string; recommendations: string[];
  customer_impact: { duration_s: number; failed_writes: number } | null;
  timeline: { step: string; title: string; status: string; duration_s: number | null; started_at: string | null }[];
}
export interface Run {
  id: number; kind: "failover" | "failback"; status: string; source_region: string; target_region: string; reason: string;
  incident_id: number | null; game_day_id: number | null; initiated_by: string; approved_by: string | null;
  created_at: string; approved_at: string | null; completed_at: string | null; metrics: RunMetrics; error: string | null;
  steps: Step[]; plan?: Plan; report?: RunReport | null;
}
export interface PlanStep { order: number; title: string; action: string; owner: string; est_s: number; automated: boolean }
export interface Plan {
  scenario: string; recommendation: string; rationale: string; source_region: string; target_region: string | null;
  steps: PlanStep[]; estimated_rto_s: number | null; estimated_rpo_s: number | null; rto_target_s: number; rpo_target_s: number;
  within_rto: boolean | null; within_rpo: boolean | null; preconditions: { check: string; ok: boolean; detail: string }[]; risks: string[];
}
export interface Backup {
  id: number; region: string; created_at: string; created_by: string; size_bytes: number; record_count: number; max_seq: number;
  checksum_sha256: string; status: "created" | "verified" | "corrupt"; verified_at: string | null; object: string;
  verification: { ok?: boolean; checksum_match?: boolean; restorable?: boolean; issues?: string[]; duration_ms?: number };
}
export interface Fault { id: number; region: string; fault_type: FaultType; active: boolean; injected_at: string; injected_by: string; cleared_at: string | null; game_day_id: number | null }
export interface Incident extends IncidentLite { started_at: string; resolved_at: string | null; details: Record<string, unknown> }
export interface GameDay {
  id: number; name: string; scenario: FaultType; target_region: string; status: string; phase: string; auto_approve: boolean;
  include_failback: boolean; created_by: string; started_at: string; finished_at: string | null; failover_run_id: number | null;
  failback_run_id: number | null; timeline: { at: string; phase: string; detail: string }[];
  report: {
    passed: boolean; detection_s: number | null; findings: string[]; readiness_before: number | null; readiness_after: number;
    failover: { id: number; status: string; rto_s: number | null; rpo_s: number | null; data_loss_txns: number | null; rto_met: boolean | null; rpo_met: boolean | null } | null;
    failback: { id: number; status: string; rto_s: number | null; divergent_discarded: number | null } | null;
  } | null;
}
export interface AuditEntry { id: number; ts: string; actor: string; action: string; target: string | null; outcome: string; details: Record<string, unknown>; ip: string | null; hash: string }
export interface Me { username: string; role: "viewer" | "operator" | "approver" | "admin"; permissions: string[] }
export interface CopilotAction { action: string; method: string; path: string; reason: string; permitted: boolean }
export interface CopilotReply { answer: string; tools_used: string[]; suggested_actions: CopilotAction[]; engine: string; note?: string }
