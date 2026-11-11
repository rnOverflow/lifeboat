# Failback #2: azure-eastus -> aws-us-east-1

*Status:* **completed**  
*Reason:* AWS recovered (e2e demo)  
*Initiated by:* operator | *Approved by:* approver

## Objectives

| Objective | Target | Measured | Result |
|---|---|---|---|
| RTO | 300s | 0.08s | PASS |
| RPO | 30s | 0.00s | PASS |

## Phase breakdown

- Detection: n/a
- Decision/approval: 0.12s
- Execution: 0.09s

## Data loss

- Lost acknowledged transactions: 0
- Loss window: 0.00s
- Planned switchover: True

## Customer impact (synthetic workload)

- Write unavailability: 0.00s, failed writes: 0

## Timeline

| Step | Status | Duration | Started |
|---|---|---|---|
| Confirm primary recovered | succeeded | 0.00s | 2026-11-11T13:48:19.437714+00:00 |
| Resync recovered primary | succeeded | 0.04s | 2026-11-11T13:48:19.441231+00:00 |
| Validate replication caught up | succeeded | 0.02s | 2026-11-11T13:48:19.477713+00:00 |
| Approval gate | succeeded | 0.07s | 2026-11-11T13:48:19.493731+00:00 |
| Drain writes & swap roles | succeeded | 0.07s | 2026-11-11T13:48:19.564793+00:00 |
| Switch global traffic | succeeded | 0.01s | 2026-11-11T13:48:19.633067+00:00 |
| Verify recovery | succeeded | 0.01s | 2026-11-11T13:48:19.640048+00:00 |

## Recommendations

- Recovery met all objectives. Keep running game days to keep this proven.
