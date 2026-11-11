# Failback #2: azure-eastus -> aws-us-east-1

*Status:* **completed**  
*Reason:* AWS recovered (e2e demo)  
*Initiated by:* operator | *Approved by:* approver

## Objectives

| Objective | Target | Measured | Result |
|---|---|---|---|
| RTO | 300s | 0.03s | PASS |
| RPO | 30s | 0.00s | PASS |

## Phase breakdown

- Detection: n/a
- Decision/approval: 0.03s
- Execution: 0.04s

## Data loss

- Lost acknowledged transactions: 0
- Loss window: 0.00s
- Planned switchover: True

## Customer impact (synthetic workload)

- Write unavailability: 4.19s, failed writes: 4

## Timeline

| Step | Status | Duration | Started |
|---|---|---|---|
| Confirm primary recovered | succeeded | 0.00s | 2026-11-11T18:44:06.022772+05:30 |
| Resync recovered primary | succeeded | 0.01s | 2026-11-11T18:44:06.024592+05:30 |
| Validate replication caught up | succeeded | 0.01s | 2026-11-11T18:44:06.037019+05:30 |
| Approval gate | succeeded | 0.02s | 2026-11-11T18:44:06.041714+05:30 |
| Drain writes & swap roles | succeeded | 0.03s | 2026-11-11T13:14:06.059037+00:00 |
| Switch global traffic | succeeded | 0.00s | 2026-11-11T13:14:06.083843+00:00 |
| Verify recovery | succeeded | 0.00s | 2026-11-11T13:14:06.088233+00:00 |

## Recommendations

- Recovery met all objectives. Keep running game days to keep this proven.
