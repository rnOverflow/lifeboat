.PHONY: help env install db migrate api web test test-backend test-frontend lint validate demo up down

help: ## Show targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'

env: ## Generate .env with random secrets (local only)
	./scripts/dev-env.sh && cp .env backend/.env

install: ## Install backend + frontend dependencies
	cd backend && uv venv -p 3.12 && uv pip install -e ".[dev,llm]"
	cd frontend && npm ci

migrate: ## Apply database migrations
	cd backend && .venv/bin/alembic upgrade head

api: ## Run the API + DR engine on :8000
	cd backend && .venv/bin/uvicorn --factory app.main:app_factory --port 8000

web: ## Run the dashboard on :5173
	cd frontend && npm run dev

test: test-backend test-frontend ## Run all tests

test-backend: ## Unit, integration and end-to-end DR tests (needs PostgreSQL)
	cd backend && .venv/bin/pytest -q

test-frontend:
	cd frontend && npm test && npm run typecheck

lint:
	cd backend && .venv/bin/ruff check . && .venv/bin/ruff format --check .
	cd frontend && npx oxlint src

validate: ## Terraform + Kubernetes validation
	terraform fmt -recursive -check infra/terraform
	for d in infra/terraform/modules/* infra/terraform/envs/*; do terraform -chdir=$$d init -backend=false -input=false >/dev/null && terraform -chdir=$$d validate || exit 1; done
	for o in base overlays/aws overlays/azure; do kubectl kustomize deploy/k8s/$$o | kubeconform -strict -summary -ignore-missing-schemas -; done

demo: ## Live DR demo against a running API
	python3 scripts/e2e_demo.py

up: ## Full stack in Docker (API, web, Postgres, Prometheus, Grafana, OTel, Jaeger)
	docker compose up -d --build

down:
	docker compose down
