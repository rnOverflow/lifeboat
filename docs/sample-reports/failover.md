# Failover #1: aws-us-east-1 -> azure-eastus

*Status:* **completed**  
*Reason:* auto: AWS us-east-1: region outage (app, database, network failing for 3 probes)  
*Initiated by:* system:monitor | *Approved by:* approver

## Objectives

| Objective | Target | Measured | Result |
|---|---|---|---|
| RTO | 300s | 6.78s | PASS |
| RPO | 30s | 2.89s | PASS |

## Phase breakdown

- Detection: 3.04s
- Decision/approval: 3.61s
- Execution: 0.13s

## Data loss

- Lost acknowledged transactions: 2
- Loss window: 2.89s
- Planned switchover: False

## Customer impact (synthetic workload)

- Write unavailability: 6.89s, failed writes: 4

## Timeline

| Step | Status | Duration | Started |
|---|---|---|---|
| Detect & confirm failure | succeeded | 0.14s | 2026-11-11T13:48:14.210356+00:00 |
| Check RTO/RPO budget | succeeded | 0.09s | 2026-11-11T13:48:14.355342+00:00 |
| Validate secondary region | succeeded | 0.05s | 2026-11-11T13:48:14.442400+00:00 |
| Approval gate | succeeded | 2.25s | 2026-11-11T13:48:14.491943+00:00 |
| Fence primary & promote secondary | succeeded | 0.07s | 2026-11-11T13:48:16.742883+00:00 |
| Switch global traffic | succeeded | 0.01s | 2026-11-11T13:48:16.807763+00:00 |
| Verify recovery | succeeded | 0.01s | 2026-11-11T13:48:16.816652+00:00 |

## Recommendations

- 2 acknowledged transactions were not replicated before promotion. Reconcile them from the old primary's divergent set during failback.
