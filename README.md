# Lifeboat: Multi-Cloud Disaster Recovery Platform

Lifeboat is a DR control plane for an application that runs **active/standby across AWS (primary) and Azure
(secondary)**. It continuously monitors both clouds and detects failures with hysteresis. It also scores
recovery readiness and generates recovery plans. It runs **controlled, approval-gated failover and failback**
and **measures the real RTO and RPO** of every recovery.

```
Healthy AWS → AWS outage → detect → check RTO/RPO → validate Azure → approve → promote Azure
→ switch traffic → verify → measure RTO/RPO → report → AWS recovers (fenced) → resync → failback → verify
```

That whole path runs locally without cloud credentials, against a simulation with **real state**. A synthetic
customer writes transactions, replication is asynchronous with real lag, and promotion and fencing change
database roles. Data loss is *measured* from acknowledged writes that never reached the standby. On failback, the
resync independently confirms the same number of divergent writes.

| | |
|---|---|
| Backend | Python 3.12, FastAPI, SQLAlchemy 2, PostgreSQL 16, Alembic |
| Frontend | React 19, TypeScript, Tailwind 4, TanStack Query, Recharts |
| Infra | Docker, Kubernetes (Kustomize, EKS/AKS overlays), Terraform (AWS + Azure + Route 53) |
| Observability | Prometheus metrics + alert rules, Grafana dashboard, OpenTelemetry traces → Jaeger |
| AI | Optional DR Copilot with read-only tools (Claude), rule-based fallback |

## Features

- **Provider abstraction**: `RegionProvider` / `TrafficManager` interfaces with `local`, `aws` and `azure`
  implementations. The DR engine never imports a cloud SDK.
- **Failure detection**: probes the app, database and network every second, declares a region DOWN after 3
  consecutive failures and UP after 2 good probes, and classifies the incident (region outage, database, network,
  service).
- **Replication monitoring**: tracks lag and *RPO exposure*, the data that would be lost if the active region
  died right now. It opens an incident when the lag budget is breached.
- **Backups with integrity validation**: SHA-256 plus a test restore. A simulator action injects bit-rot to prove
  that verification catches it.
- **RTO/RPO policy** and real measurements, broken down into detect / decide / execute phases, plus customer
  impact from the synthetic workload.
- **Readiness score (0-100)** across standby health, replication, backups, test cadence and proven RTO, with
  explicit blockers.
- **Automated recovery plans** derived only from observed state: failover, self-heal, failback, repair standby,
  repair replication, or restore from backup.
- **Controlled failover** as a 7-step state machine with an approval gate and a two-person rule. Lease-based
  fencing of the old primary prevents split-brain.
- **Failback**: resync (discarding divergent writes), wait for lag, approval, then a graceful drain and role swap
  with zero data loss.
- **Failure simulator** for cloud outage, database, network, service and replication failures, plus backup
  corruption.
- **DR Game Days**: inject, detect, fail over, recover, fail back, then produce a pass/fail report with findings.
- **Security**: JWT auth, bcrypt, 4-role RBAC (viewer/operator/approver/admin), login rate limiting, and a
  **hash-chained tamper-evident audit log**.
- **Self-healing**: service failures trigger an automatic restart *before* any regional failover is considered.
- **DR Copilot**: answers from live data via read-only tools filtered by RBAC. It can only *propose* actions,
  which the human executes with their own permissions.
- **Leader election**: multiple API replicas run safely because only the holder of a PostgreSQL advisory lock
  runs the DR engine.

## Quick start (local, no cloud credentials)

Prerequisites: Python 3.12 with [uv](https://docs.astral.sh/uv/), Node 20+, and PostgreSQL 16 on localhost.

```bash
make env          # writes .env with random JWT secret + bootstrap passwords (never committed)
make install
createdb lifeboat # or point LIFEBOAT_DATABASE_URL at any PostgreSQL
make migrate
make api          # API + DR engine on :8000
make web          # dashboard on :5173 (separate terminal)
```

Sign in with a user from `.env` (`admin`, `operator`, `approver`, `viewer`). To run the full scripted DR demo
against the live server:

```bash
make demo
```

### Everything in Docker

```bash
make env && docker compose up -d --build
```

| URL | |
|---|---|
| http://localhost:8080 | Dashboard |
| http://localhost:8000/api/docs | OpenAPI |
| http://localhost:3000 | Grafana (`admin` / `GRAFANA_ADMIN_PASSWORD` from `.env`) |
| http://localhost:9090 | Prometheus (8 DR alert rules) |
| http://localhost:16686 | Jaeger: one span per failover step |

## Measured results (live demo, real time, Docker stack)

| | Target | Measured |
|---|---|---|
| Detection (first failed probe → incident) | – | 3.0 s |
| Failover RTO (first failed probe → verified recovery, incl. human approval) | 300 s | 6.8 s |
| Failover RPO (unplanned, async replication) | 30 s | 2.9 s / 2 txns |
| Divergent writes discarded at failback resync | – | 2 (matches measured loss) |
| Failback data loss (graceful drain) | 0 | 0 txns |

See [docs/sample-reports](docs/sample-reports) for the generated reports.

## Testing

```bash
make test        # 80 backend tests (unit + API integration + end-to-end DR + game days) + frontend tests
make lint
make validate    # terraform fmt/validate (4 configs) + kustomize/kubeconform (3 overlays)
```

- **Unit**: readiness, planner, reports, RBAC/JWT, local provider physics, detection hysteresis, orchestrator
  rules (two-person rule, reject/cancel, validation failure, planned switchover), audit tamper detection, AWS
  adapters against **moto**, Azure adapters against mocked SDK clients, and the copilot (including a mocked LLM
  tool loop).
- **Integration**: every endpoint over HTTP against PostgreSQL, including RBAC denials (parametrized and
  audited), login rate limiting, backup corruption and policy validation.
- **End-to-end**: `tests/e2e/test_dr_demo.py` runs the required demo through the API with a deterministic clock.
  `test_gameday.py` covers every failure scenario. `scripts/e2e_demo.py` runs the same path over real HTTP in real
  time, and CI runs it against the Docker Compose stack.

## Repository layout

```
backend/app/
  providers/   base.py (contracts) · local.py (simulation) · aws.py · azure.py · pgrepl.py (cross-cloud PG)
  engine/      monitor · orchestrator (failover/failback) · readiness · planner · report · gameday · runtime
  api/         FastAPI routers, RBAC dependencies
  copilot/     tool-restricted DR copilot
  audit.py     hash-chained audit log
frontend/src/  dashboard (overview, failover console, simulator, game days, backups, incidents, copilot, audit)
infra/terraform/  modules: aws-region, azure-region, global-dns · envs/prod
deploy/k8s/       kustomize base + EKS / AKS overlays (External Secrets, IRSA / workload identity)
deploy/monitoring/  Prometheus, alert rules, Grafana dashboard, OTel collector
docs/             architecture · runbook · failover/failback procedures · sample reports
```

## Documentation

- [Architecture](docs/architecture.md): components, provider abstraction, state machines, measurement
  methodology, security model, trade-offs
- [DR runbook](docs/runbook.md): alert → action playbooks
- [Failover & failback](docs/failover-failback.md): step-by-step procedures and the reasoning behind each step

## Running against real clouds

Set `LIFEBOAT_PROVIDER_MODE=cloud` and supply the values from `terraform output lifeboat_settings` (see
`.env.example`). The cloud adapters use RDS / Route 53 / RDS snapshots on AWS and PostgreSQL Flexible Server /
Azure backups on Azure. Cross-cloud data movement uses PostgreSQL logical replication. The adapters are covered by
moto and mock-based tests but **have not been exercised against live AWS/Azure accounts**: that needs your
credentials and incurs cost. See *Limitations* in the architecture doc.

