# Failover #1: aws-us-east-1 -> azure-eastus

*Status:* **completed**  
*Reason:* auto: AWS us-east-1: region outage (app, database, network failing for 3 probes)  
*Initiated by:* system:monitor | *Approved by:* approver

## Objectives

| Objective | Target | Measured | Result |
|---|---|---|---|
| RTO | 300s | 4.17s | PASS |
| RPO | 30s | 2.05s | PASS |

## Phase breakdown

- Detection: 2.06s
- Decision/approval: 2.07s
- Execution: 0.04s

## Data loss

- Lost acknowledged transactions: 2
- Loss window: 2.05s
- Planned switchover: False

## Timeline

| Step | Status | Duration | Started |
|---|---|---|---|
| Detect & confirm failure | succeeded | 0.00s | 2026-11-11T18:44:01.742439+05:30 |
| Check RTO/RPO budget | succeeded | 0.01s | 2026-11-11T18:44:01.745387+05:30 |
| Validate secondary region | succeeded | 0.01s | 2026-11-11T18:44:01.755098+05:30 |
| Approval gate | succeeded | 2.02s | 2026-11-11T18:44:01.760272+05:30 |
| Fence primary & promote secondary | succeeded | 0.02s | 2026-11-11T13:14:03.781626+00:00 |
| Switch global traffic | succeeded | 0.00s | 2026-11-11T13:14:03.803150+00:00 |
| Verify recovery | succeeded | 0.01s | 2026-11-11T13:14:03.807364+00:00 |

## Recommendations

- 2 acknowledged transactions were not replicated before promotion. Reconcile them from the old primary's divergent set during failback.
