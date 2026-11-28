# Command contract (CLAUDE.md §7). Targets marked (Pn) become real in that phase.
SHELL := /bin/bash
TAG ?= $(shell git rev-parse --short=7 HEAD 2>/dev/null || echo dev)
NS  := idea-validator
SERVICES := gateway profile orchestrator scouts tools kb analyst ideation validators scorer verdict critic agentops

.PHONY: dev-env secrets k8s-gen k8s-verify help setup lint typecheck test test-verdict test-retrieval up down migrate seed images jenkins k8s-up deploy smoke netpol-test eval eval-results eval-gate replay
help: ## list targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  %-16s %s\n",$$1,$$2}'
setup: ## install dev deps + hooks (P0)
	uv sync --all-packages && uv run pre-commit install && { [ ! -f services/ui/package.json ] || (cd services/ui && npm ci); }
lint: ## ruff + import layering + eslint
	uv run ruff check . && uv run ruff format --check . && uv run lint-imports && { [ ! -f services/ui/package.json ] || (cd services/ui && npm run lint); }
typecheck: ## mypy + tsc
	uv run mypy && { [ ! -f services/ui/package.json ] || (cd services/ui && npm run typecheck); }
test: ## unit/component/behaviour tests
	uv run pytest -m "not integration and not retrieval" && { [ ! -f services/ui/package.json ] || (cd services/ui && npm test -- --run); }
test-verdict: ## M11 unit + golden vectors (P2)
	uv run pytest services/verdict -q
test-retrieval: ## retrieval quality gate (P3): indexes the snapshot with the real models, several minutes
	uv run pytest eval/retrieval -q
dev-env: ## create the missing dev credentials in the git-ignored .env (random, never committed)
	uv run python scripts/create_secrets.py --dev-env
up: dev-env ## dev stack (postgres, redis, qdrant via docker compose), waits until healthy
	docker compose up -d --wait
down: ## stop the dev stack; data volumes are kept (docker compose down -v deletes them)
	docker compose down
migrate: ## alembic upgrade on the compose Postgres (run `make up` first)
	set -a && . ./.env && set +a && uv run --package idea-validator-profile alembic -c services/profile/alembic.ini upgrade head && uv run --package idea-validator-kb alembic -c services/kb/alembic.ini upgrade head && uv run --package idea-validator-agentops alembic -c services/agentops/alembic.ini upgrade head
seed: ## restore the newest data/snapshots/cards-*.jsonl.gz into the kind cluster via a seed Job (M6i, P3-T09)
	./scripts/seed.sh $(TAG)
jenkins: ## start Jenkins in Docker on 127.0.0.1:8085 (needs the kind cluster)
	scripts/jenkins_up.sh
images: ## build and push every module image to the local registry, tagged TAG (default: commit SHA)
	for s in $(SERVICES); do docker build -q -f services/$$s/Dockerfile -t localhost:5001/$$s:$(TAG) . && docker push -q localhost:5001/$$s:$(TAG); done
	@if [ -f services/ui/package.json ]; then docker build -q -t localhost:5001/ui:$(TAG) services/ui && docker push -q localhost:5001/ui:$(TAG); fi
	@if [ -f jobs/ingestion/Dockerfile ]; then docker build -q -f jobs/ingestion/Dockerfile -t localhost:5001/ingestion:$(TAG) . && docker push -q localhost:5001/ingestion:$(TAG); fi
	@if [ -f jobs/evaluation/Dockerfile ]; then docker build -q -f jobs/evaluation/Dockerfile -t localhost:5001/evaluation:$(TAG) . && docker push -q localhost:5001/evaluation:$(TAG); fi
k8s-up: ## kind + calico + registry + ingress + metrics-server (P1)
	./scripts/k8s_up.sh
k8s-gen: ## regenerate k8s/ manifests from scripts/gen_k8s.py (a test fails if they drift)
	uv run python scripts/gen_k8s.py
k8s-verify: ## prove registry, ingress, metrics and NetworkPolicy enforcement on the kind cluster (P1)
	./scripts/verify_cluster.sh
secrets: ## create/update the app-secrets Secret from .env (keeps existing random values)
	./scripts/create_secrets.sh
deploy: ## kustomize apply with images at TAG (P1)
	./scripts/deploy.sh $(TAG)
smoke: ## smoke test through ingress (P1)
	./scripts/smoke_test.sh
netpol-test: ## non-M5 egress fails, M5 succeeds (P1)
	./scripts/netpol_test.sh
eval: ## run the evaluation Job on the cluster, save eval/results/<ts>.json, gate on baseline.json (exit 2 without eval/labelled/labelled_set.jsonl)
	./scripts/run_eval.sh
eval-results: ## copy the newest evaluation Job result (its last log line) to eval/results/<ts>.json
	./scripts/eval_results.sh
eval-gate: ## compare the newest eval/results/<ts>.json with baseline.json, no cluster run
	./scripts/check_eval.sh
replay: ## replay a stored run through the pure M11 engine: make replay RUN=run_90 (TRACE_DIR=dir for local files, else kubectl exec into agentops)
	@case "$$RUN" in ""|*[!A-Za-z0-9_-]*) echo "usage: make replay RUN=<run_id> (letters, digits, _ and - only)" >&2; exit 2;; esac; \
	if [ "$${#RUN}" -gt 64 ]; then echo "run id longer than 64 characters" >&2; exit 2; fi; \
	if [ -n "$$TRACE_DIR" ]; then uv run --package idea-validator-agentops python -m agentops.replay --config-dir config -- "$$RUN"; \
	else kubectl -n idea-validator exec deploy/agentops -- python -m agentops.replay -- "$$RUN"; fi
