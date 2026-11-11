#!/usr/bin/env python3
"""Live end-to-end DR demo against a running Lifeboat API (real time, real HTTP).

Healthy AWS -> AWS outage -> detect -> RTO/RPO check -> validate Azure -> approve -> promote -> switch ->
verify -> RTO/RPO -> report -> recover AWS -> failback -> verify

Usage: scripts/e2e_demo.py [--api http://localhost:8000]   (reads passwords from .env)
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_env() -> dict[str, str]:
    env = {}
    for line in (ROOT / ".env").read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env


class Api:
    def __init__(self, base: str) -> None:
        self.base = base.rstrip("/")
        self.tokens: dict[str, str] = {}

    def call(self, method: str, path: str, user: str | None = None, body: dict | None = None, raw: bool = False):
        req = urllib.request.Request(self.base + path, method=method,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Content-Type": "application/json"})
        if user:
            req.add_header("Authorization", f"Bearer {self.tokens[user]}")
        try:
            with urllib.request.urlopen(req, timeout=30) as r:  # noqa: S310 - local URL from CLI arg
                data = r.read().decode()
                return data if raw else json.loads(data)
        except urllib.error.HTTPError as e:
            raise SystemExit(f"{method} {path} -> {e.code}: {e.read().decode()}") from e

    def login(self, user: str, password: str) -> None:
        self.tokens[user] = self.call("POST", "/api/v1/auth/login",
                                      body={"username": user, "password": password})["access_token"]


def step(msg: str) -> None:
    print(f"\n\033[1;36m==> {msg}\033[0m", flush=True)


def wait_for(desc: str, fn, timeout: float = 60.0, interval: float = 0.5):
    t0 = time.time()
    while time.time() - t0 < timeout:
        v = fn()
        if v:
            print(f"    {desc} after {time.time() - t0:.1f}s")
            return v
        time.sleep(interval)
    raise SystemExit(f"timed out waiting for: {desc}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://localhost:8000")
    args = ap.parse_args()
    env = load_env()
    api = Api(args.api)
    for u in ("operator", "approver", "viewer"):
        api.login(u, env[f"LIFEBOAT_BOOTSTRAP_{u.upper()}_PASSWORD"])

    def status():
        return api.call("GET", "/api/v1/status", "viewer")

    def regions():
        return {r["name"]: r for r in status()["regions"]}

    st = status()
    aws, az = st["designated_primary"], next(r["name"] for r in st["regions"] if r["name"] != st["designated_primary"])

    step("0. Baseline: healthy AWS primary, Azure replica, verified backup")
    if st["active_region"] != aws or st["active_run"]:
        raise SystemExit("system not in steady state (traffic not on AWS or a run is active) - reset first")
    wait_for("both regions UP", lambda: all(r["status"] == "up" for r in regions().values()))
    b = api.call("POST", "/api/v1/backups", "operator", {})
    b = api.call("POST", f"/api/v1/backups/{b['id']}/verify", "operator")
    print(f"    backup #{b['id']}: {b['record_count']} records, sha256 {b['checksum_sha256'][:12]}..., {b['status']}")
    rd0 = status()["readiness"]
    print(f"    readiness {rd0['score']} ({rd0['grade']}), RPO exposure {status()['rpo_exposure_s']}s")

    step("1. Simulate AWS region outage")
    api.call("POST", "/api/v1/simulator/faults", "operator", {"region": aws, "fault_type": "cloud_outage"})
    t_fault = time.time()

    step("2. Detect failure (3 consecutive failed probes)")
    wait_for("AWS declared DOWN", lambda: regions()[aws]["status"] == "down")
    inc = api.call("GET", "/api/v1/incidents?status=open", "viewer")[0]
    print(f"    incident #{inc['id']}: {inc['summary']}")

    step("3-4. Auto-initiated failover: RTO/RPO check + Azure validation, parked at approval gate")
    run = wait_for("run awaiting approval", lambda: next(
        (r for r in api.call("GET", "/api/v1/runs?limit=1", "viewer") if r["status"] == "awaiting_approval"), None))
    steps = {s["name"]: s for s in api.call("GET", f"/api/v1/runs/{run['id']}", "viewer")["steps"]}
    a = steps["assess"]["output"]
    print(f"    RTO budget {a['rto_target_s']}s (remaining {a['rto_remaining_s']}s), "
          f"RPO exposure {a['rpo_exposure_s']}s vs target {a['rpo_target_s']}s")
    print(f"    Azure validation: {steps['validate']['output']['checks']}")

    step("5-8. Approver approves -> promote Azure -> switch traffic -> verify")
    time.sleep(1.5)
    done = api.call("POST", f"/api/v1/runs/{run['id']}/approve", "approver", {"comment": "e2e demo"})
    if done["status"] != "completed":
        raise SystemExit(f"failover ended {done['status']}: {done['error']}")
    for s in done["steps"]:
        print(f"    [{s['status']:>9}] {s['title']}")
    st = status()
    assert st["active_region"] == az, st["active_region"]

    step("9. Measured RTO / RPO")
    m = done["metrics"]
    print(f"    RTO {m['rto_s']}s (target {m['rto_target_s']}s) -> {'MET' if m['rto_met'] else 'MISSED'}")
    print(f"    RPO {m['rpo_s']}s (target {m['rpo_target_s']}s), {m['data_loss_txns']} acked txns not replicated"
          f" -> {'MET' if m['rpo_met'] else 'MISSED'}")
    print(f"    detect {m['time_to_detect_s']}s | approve {m['time_to_approve_s']}s | execute {m['time_to_execute_s']}s"
          f" | wall clock since fault {time.time() - t_fault:.1f}s")

    step("10. Recovery report")
    md = api.call("GET", f"/api/v1/runs/{run['id']}/report.md", "viewer", raw=True)
    out = ROOT / "reports" / f"failover-{run['id']}.md"
    out.parent.mkdir(exist_ok=True)
    out.write_text(md)
    print(f"    written to {out.relative_to(ROOT)}")
    wait_for("customer writes served by Azure", lambda: status()["workload"].get("last_acked_region") == az)

    step("11. Recover AWS (it must come back FENCED, not as a second primary)")
    api.call("POST", "/api/v1/simulator/clear", "operator", {"region": aws})
    wait_for("AWS UP and fenced", lambda: regions()[aws]["status"] == "up" and regions()[aws]["db_role"] == "fenced")

    step("12. Failback: resync -> validate -> approve -> drain & swap -> switch -> verify")
    fb = api.call("POST", "/api/v1/runs/failback", "operator", {"reason": "AWS recovered (e2e demo)"})
    fb = wait_for("failback ready for approval", lambda: (
        r := api.call("GET", f"/api/v1/runs/{fb['id']}", "viewer"))["status"] == "awaiting_approval" and r)
    rs = {s["name"]: s for s in fb["steps"]}["resync"]["output"]
    print(f"    resync copied {rs['copied']} txns, discarded {rs['divergent_discarded']} divergent "
          f"(failover measured {m['data_loss_txns']} lost)")
    fb = api.call("POST", f"/api/v1/runs/{fb['id']}/approve", "approver", {"comment": "e2e demo failback"})
    if fb["status"] != "completed":
        raise SystemExit(f"failback ended {fb['status']}: {fb['error']}")
    fm = fb["metrics"]
    print(f"    failback RTO {fm['rto_s']}s (write freeze), data loss {fm['data_loss_txns']} txns")
    (ROOT / "reports" / f"failback-{fb['id']}.md").write_text(
        api.call("GET", f"/api/v1/runs/{fb['id']}/report.md", "viewer", raw=True))

    step("13. Verify steady state restored")
    wait_for("traffic on AWS, Azure replicating within budget", lambda: (
        (s := status())["active_region"] == aws
        and {r["name"]: r for r in s["regions"]}[az]["db_role"] == "replica"
        and ({r["name"]: r for r in s["regions"]}[az]["replication_lag_s"] or 99) <= 15
        and s["workload"].get("last_acked_region") == aws))
    rd = status()["readiness"]
    print(f"    readiness {rd['score']} ({rd['grade']}) - was {rd0['score']} before the drill")
    v = api.call("GET", "/api/v1/audit/verify", "approver")
    print(f"    audit chain valid={v['valid']} ({v['entries_checked']} entries)")
    assert v["valid"]
    print("\n\033[1;32mEnd-to-end DR demo PASSED\033[0m")
    return 0


if __name__ == "__main__":
    sys.exit(main())
