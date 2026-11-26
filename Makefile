# Command contract (CLAUDE.md §7). Targets marked (Pn) become real in that phase.
SHELL := /bin/bash
TAG ?= $(shell git rev-parse --short=7 HEAD 2>/dev/null || echo dev)
NS  := idea-validator
SERVICES := gateway profile orchestrator scouts tools kb analyst ideation validators scorer verdict critic agentops

.PHONY: help setup lint typecheck test test-verdict test-retrieval up down migrate seed images k8s-up deploy smoke netpol-test eval replay
help: ## list targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  %-16s %s\n",$$1,$$2}'
setup: ## install dev deps + hooks (P0)
	uv sync --all-packages && uv run pre-commit install && { [ ! -f services/ui/package.json ] || (cd services/ui && npm ci); }
lint: ## ruff + import layering + eslint
	uv run ruff check . && uv run ruff format --check . && uv run lint-imports && { [ ! -f services/ui/package.json ] || (cd services/ui && npm run lint); }
typecheck: ## mypy + tsc
	uv run mypy && { [ ! -f services/ui/package.json ] || (cd services/ui && npm run typecheck); }
test: ## unit/component/behaviour tests
	uv run pytest -m "not integration" && { [ ! -f services/ui/package.json ] || (cd services/ui && npm test -- --run); }
test-verdict: ## M11 unit + golden vectors (P2)
	uv run pytest services/verdict -q
test-retrieval: ## retrieval quality gate (P3)
	uv run pytest eval/retrieval -q
up: ## docker compose dev stack (dev only)
	docker compose up -d --build
down:
	docker compose down
migrate: ## alembic upgrade (P2)
	uv run --package profile alembic -c services/profile/alembic.ini upgrade head
seed: ## restore card snapshot (P3)
	uv run --package ingestion python -m ingestion restore data/snapshots/latest.jsonl.gz
images: ## build all module images tagged by commit SHA
	for s in $(SERVICES); do docker build -f services/$$s/Dockerfile -t localhost:5001/$$s:$(TAG) .; done
	docker build -t localhost:5001/ui:$(TAG) services/ui
	docker build -t localhost:5001/ingestion:$(TAG) jobs/ingestion
k8s-up: ## kind + calico + registry + ingress + metrics-server (P1)
	./scripts/k8s_up.sh
deploy: ## kustomize apply with images at TAG (P1)
	./scripts/deploy.sh $(TAG)
smoke: ## smoke test through ingress (P1)
	./scripts/smoke_test.sh
netpol-test: ## non-M5 egress fails, M5 succeeds (P1)
	./scripts/netpol_test.sh
eval: ## evaluation job + compare with baseline (P9)
	kubectl apply -f k8s/base/eval/eval-job.yaml -n $(NS) && ./scripts/check_eval.sh
replay: ## replay a stored run: make replay RUN=run_90 (P9)
	uv run --package agentops python -m agentops.replay $(RUN)
