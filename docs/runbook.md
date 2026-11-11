# DR Runbook

Each section maps to a Prometheus alert or Lifeboat incident kind. Every action in this runbook is available
in the dashboard and the API, and every action is audited.

**Roles.** The *incident commander* (IC) owns the decision, an *operator* executes it, and an *approver*
(not the initiator) authorises promotion.

**First 2 minutes, every time:**
1. Open the dashboard Overview. Check traffic location, region cards, readiness blockers and the
   *Recommended action* panel.
2. Ask the Copilot "What is happening and what should we do?" It summarises live state and plan with numbers.
3. Declare the incident in your paging tool and link the Lifeboat incident number.

---

## 1. Active region down

*Alert:* `ActiveRegionDown` · *Incident:* `region_outage` / `database_failure` / `network_failure` (critical)

Lifeboat has already confirmed the failure (3 consecutive failed probes) and, with `auto_initiate_failover`,
created a failover run that is parked at the approval gate after passing **detect → assess → validate**.

1. Open **Failover → run #N** and read:
   - *Check RTO/RPO budget*: `rto_remaining_s` and `rpo_exposure_s`. If `rpo_at_risk` is true, a failover will
     lose more data than the RPO allows. Decide consciously (see 1a).
   - *Validate secondary*: all checks must be true. If validation failed, go to §5.
2. Confirm it is not a monitoring false positive. Is the region also down in the provider status page or your
   own probes? Lifeboat's hysteresis makes flapping unlikely but not impossible.
3. **Approver** clicks *Approve failover* with a comment. The run then fences the old primary, promotes the
   standby, switches DNS and verifies with a synthetic write+read. Typical execution is under 1 s plus DNS TTL.
4. Verify: Overview shows traffic on Azure and *Writes succeeding*. The incident moves to `mitigated`.
5. Download the report (Failover → Report .md) and attach it to the incident.

**1a. RPO at risk.** If exposure exceeds the RPO target, you are choosing between more downtime (wait for the
primary) and more data loss (fail over now). Choose with the business owner and record the decision in the
approval comment. To wait, *Reject* the run. Lifeboat will not re-initiate for the same incident.

**Do not** manually restart the old primary's database as writable. It must return fenced and be resynced (§3).

## 2. Service failure (application tier only)

*Incident:* `service_failure`

Lifeboat restarts the application tier automatically (self-heal) **before** considering failover. Check the
incident details for `auto_remediation`. If the region is still down 15 s after the restart, the incident
escalates and a failover run is created (→ §1).

If restarts loop, the fault is probably in the release: roll back the deployment rather than failing over.
Failing over a bad release just moves the outage.

## 3. Primary recovered: fail back

*Plan recommendation:* `failback`

1. Overview should show AWS `UP` with database role `FENCED`. If it shows `PRIMARY`, stop: something bypassed
   fencing. Escalate.
2. Operator starts **Failback** with a reason. Lifeboat then:
   - *Resync*: rebuilds AWS from Azure and **discards divergent writes**. Their sequence numbers are in the step
     output. Hand them to the data owner for reconciliation (they are the writes lost at failover).
   - *Validate*: waits until replication lag ≤ budget.
3. Approver approves. The drain is graceful: Azure is fenced, AWS catches up fully, roles swap, DNS switches
   back, and the result is verified. Expected data loss is 0.
4. Confirm readiness returns to `ready` and Azure is a `REPLICA` with healthy lag.

Schedule failback in a low-traffic window, because writes pause during the drain.

## 4. Replication lag / RPO exposure

*Alerts:* `ReplicationLagAboveBudget`, `RpoExposureBreachesTarget` · *Incident:* `replication_lag` (warning)

Production is fine, but a failover now would lose data.

1. Check the replication path between clouds: network, subscription state, replica disk/CPU.
   On PostgreSQL run `SELECT * FROM pg_stat_subscription;` on the replica and
   `SELECT * FROM pg_replication_slots;` on the primary.
2. If the replica is broken beyond repair, rebuild it with `resync` (cloud: re-create the subscription with
   `copy_data = true`).
3. The incident resolves automatically when lag returns within budget.

## 5. Standby region down / validation failed

*Alert:* `StandbyRegionDown` · readiness blocker *No healthy standby*

DR protection is lost. Production is unaffected, but you have no failover target.

- Treat as **major**: restore the standby before the next change window.
- If the *active* region also fails, follow §6.

## 6. Both regions impaired

*Plan recommendation:* `restore_from_backup`

1. Declare a major incident and engage both cloud providers.
2. Identify the newest **verified** backup (Backups page). Unverified backups are a last resort.
3. Restore into whichever region returns first and point traffic at it. RPO is the backup age.

## 7. Backup integrity failure

*Alert:* `NoRecentVerifiedBackup` · Backups page shows `corrupt`

1. Take a fresh backup immediately and verify it.
2. Investigate the storage layer for the corrupt object (checksum mismatch means modification or bit-rot).
3. Keep the corrupt object for forensics. Versioned buckets retain it.

## 8. DR engine stalled

*Alert:* `DrEngineStalled` · `/readyz` returns 503

Detection is blind. Check the API pods' logs. If the leader replica is wedged, delete the pod: another replica
acquires the advisory lock within one tick. Until the engine recovers, monitor the providers' consoles manually.

---

## Game days (monthly)

1. Game Days → choose a scenario (rotate through all five) and target region.
2. Run with **pre-approval off** at least quarterly so that the human approval path, which usually dominates RTO,
   is exercised.
3. A game day passes only if the fault was detected, every run completed, and RTO/RPO targets were met.
   Findings feed the backlog.
