# Failover & Failback Procedures

This document describes exactly what each automated step does, why it exists, and what it records.
Implementation: `backend/app/engine/orchestrator.py`.

## Failover (`POST /api/v1/runs/failover` or auto-initiated)

| # | Step | What happens | Fails / waits when |
|---|---|---|---|
| 1 | **Detect & confirm** | Reads the monitor's verdict on the source region. DOWN means unplanned failover: the outage start is the incident's first failed probe. UP means a **planned switchover**. | Waits while the region is only DEGRADED (below the threshold) |
| 2 | **Check RTO/RPO budget** | Records the RTO target, time already elapsed, estimated time to recover, and current RPO exposure (client-acked vs replicated). Flags `rto_at_risk` / `rpo_at_risk`. | Never: informational, shown to the approver |
| 3 | **Validate secondary** | Probes the target directly (not cached): app, DB and network healthy, DB reachable, role = replica. | **Fails the run** if any check is false. Lifeboat never promotes into a broken region |
| 4 | **Approval gate** | Run status becomes `awaiting_approval`. An approver or admin who is not the initiator approves or rejects. If `require_approval=false`, it is auto-approved as `policy:auto-approve`. | Waits indefinitely, or is rejected |
| 5 | **Fence & promote** | (a) Revoke the source's write lease (fence). (b) If the source DB is still reachable, **drain** it: replicate everything, for zero data loss. (c) Promote the target. (d) If drained, demote the source to replica. (e) Measure RPO and data loss from the client ack ledger. | Provider errors fail the run, and the error is recorded |
| 6 | **Switch traffic** | `TrafficManager.switch_to(target)`: a Route 53 CNAME UPSERT in cloud mode. | Provider errors |
| 7 | **Verify recovery** | Traffic points at the target, the target probe is healthy, and a **synthetic write + read** through the target succeeds. Sets `service_restored_at`. | Retries up to 10 ticks, then fails |

On completion Lifeboat computes metrics (RTO, RPO, phases), builds the report (`GET /runs/{id}/report.md`),
marks the incident `mitigated`, and exports `lifeboat_last_rto_seconds` / `lifeboat_last_rpo_seconds`.

### Why fence before promote?

A region that is unreachable from the control plane may still be running and accepting writes from clients that
can reach it. Fencing is lease-based in the control plane, so it succeeds even when the region is unreachable.
It ensures that when the old primary comes back it is **read-only** and cannot form a second primary. In cloud
mode, fencing an unreachable database is recorded as *deferred*, and the runbook requires confirming it on
return.

### Planned switchover vs. unplanned failover

| | Planned (source healthy) | Unplanned (source down) |
|---|---|---|
| Drain before promote | yes | impossible |
| Data loss | 0 | unreplicated tail (measured) |
| Old primary afterwards | replica, no resync needed | fenced, resync required |
| RTO basis | write freeze → verified | first failed probe → verified |

## Failback (`POST /api/v1/runs/failback`)

Only valid while traffic is **not** on the designated primary.

| # | Step | What happens |
|---|---|---|
| 1 | **Confirm primary recovered** | Waits until the monitor reports the designated primary UP (2 consecutive healthy probes). |
| 2 | **Resync** | If the recovered DB is FENCED, rebuild it from the current primary. Rows whose sequence number is missing on the current primary, or whose content differs, are **divergent** and discarded. Their count and sequence numbers are recorded. The DB becomes a replica. |
| 3 | **Validate replication** | Waits until lag ≤ `max_replication_lag_s` and the target is healthy. |
| 4 | **Approval gate** | Same rules as failover. |
| 5 | **Drain & swap roles** | Fence the current primary, catch the target up completely, promote it, and demote the old one to replica. Data loss 0. |
| 6 | **Switch traffic** | Back to the designated primary. |
| 7 | **Verify** | Synthetic write + read through the primary. |

### Reconciling divergent writes

The divergent writes discarded in step 2 are exactly the acknowledged writes lost at failover. The failover
report's `data_loss_txns` and the failback's `divergent_discarded` should be equal. Any mismatch indicates a
measurement or replication problem worth investigating. The sequence numbers are in the resync step output for
manual reconciliation.

## Cancel / reject semantics

- **Reject** (approver): the run ends `rejected`, no state changes, and traffic stays put.
- **Cancel** (operator): allowed only before promotion starts. After promotion, the safe way back is to complete
  the run and then fail back.
- Only one recovery run can be active at a time.

## API cheat sheet

```bash
TOKEN=$(curl -s localhost:8000/api/v1/auth/login -H 'content-type: application/json' \
  -d '{"username":"operator","password":"…"}' | jq -r .access_token)
H="Authorization: Bearer $TOKEN"

curl -s -H "$H" localhost:8000/api/v1/plan | jq                       # what would Lifeboat do?
curl -s -H "$H" -X POST localhost:8000/api/v1/runs/failover \
  -H 'content-type: application/json' -d '{"reason":"planned DR test"}'
curl -s -H "$APPROVER" -X POST localhost:8000/api/v1/runs/7/approve \
  -H 'content-type: application/json' -d '{"comment":"go"}'
curl -s -H "$H" localhost:8000/api/v1/runs/7/report.md
```
