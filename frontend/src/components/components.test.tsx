import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { Status } from "../lib/api";
import Gauge from "./Gauge";
import Topology from "./Topology";
import { StatusBadge, statusTone } from "./ui";

const region = (name: string, cloud: "aws" | "azure", status: "up" | "down", db_role: string, lag: number | null) => ({
  name, cloud, display_name: name, designated_role: cloud === "aws" ? "primary" : "secondary", status, db_role,
  replication_lag_s: lag, last_applied_seq: 42, consecutive_failures: 0, last_probe_at: null, first_failure_at: null,
  probe: { healthy: status === "up", latency_ms: 10, app: { ok: status === "up", latency_ms: 1, detail: "HTTP 200" },
    database: { ok: status === "up", latency_ms: 1, detail: "ok" }, network: { ok: status === "up", latency_ms: 1, detail: "" } },
});

const status = (awsUp: boolean): Status => ({
  mode: "local", active_region: "aws", standby_region: "az", designated_primary: "aws", failed_over: false,
  regions: [region("aws", "aws", awsUp ? "up" : "down", "primary", 0), region("az", "azure", "up", "replica", 2.5)],
  rpo_exposure_s: 2.5,
  policy: { rto_target_s: 300, rpo_target_s: 30, max_replication_lag_s: 15, backup_max_age_s: 3600, auto_initiate_failover: true, require_approval: true, two_person_rule: true },
  readiness: { score: 90, grade: "A", status: "ready", components: [], blockers: [], protected_region: "aws", standby_region: "az" },
  workload: {}, open_incidents: [], active_run: null, latest_backup: null, measured_rto_s: [], engine: { last_tick_at: null, tick_errors: 0 }, server_time: "",
});

describe("ui", () => {
  it("maps statuses to tones", () => {
    expect(statusTone("down")).toBe("bad");
    expect(statusTone("awaiting_approval")).toBe("warn");
    expect(statusTone("verified")).toBe("ok");
    expect(statusTone("weird")).toBe("muted");
  });
  it("renders status badge text", () => {
    render(<StatusBadge value="awaiting_approval" />);
    expect(screen.getByText("awaiting approval")).toBeInTheDocument();
  });
  it("renders accessible gauge", () => {
    render(<Gauge value={77} grade="B" tone="warn" />);
    expect(screen.getByRole("img", { name: /Readiness 77 of 100, grade B/ })).toBeInTheDocument();
  });
});

describe("Topology", () => {
  it("shows replication lag when both regions are healthy", () => {
    render(<Topology st={status(true)} />);
    expect(screen.getByText("lag 2.5s")).toBeInTheDocument();
    expect(screen.getByText("serving traffic")).toBeInTheDocument();
  });
  it("shows replication stopped when primary is down", () => {
    render(<Topology st={status(false)} />);
    expect(screen.getByText("not replicating")).toBeInTheDocument();
    expect(screen.getByText("down")).toBeInTheDocument();
  });
});
