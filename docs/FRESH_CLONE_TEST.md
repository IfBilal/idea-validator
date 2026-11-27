# Fresh-clone start test (P11-T06)

Goal: someone who has never seen the repo starts it in at most 10 commands. Run it on a second laptop and record the date and result at the bottom.

Prerequisites: git, Python 3.12 via uv, Docker (or Podman) for databases and tests, Node 20+ only once `services/ui` exists. For the cluster part: kind, kubectl, kustomize and about 16 GB RAM (see `README.md`).

## A. Without a cluster (no credentials)

```bash
git clone https://github.com/IfBilal/idea-validator.git && cd idea-validator   # 1
make setup          # 2  uv sync --all-packages + pre-commit
make lint           # 3
make typecheck      # 4
make test           # 5  integration cases need Docker (testcontainers) or TEST_REDIS_URL / TEST_DATABASE_URL; they are skipped with -m "not integration"
make test-verdict   # 6
make up             # 7  Postgres, Redis, Qdrant via compose; creates a git-ignored .env
make migrate        # 8
make down           # 9
```

Automated version of A (clones to a temp dir, fails loudly, prints what it skipped): `scripts/fresh_clone_check.sh [SOURCE] [REF]`.

## B. On Kubernetes (needs a local `.env`; an LLM key only for real model calls)

```bash
cp .env.example .env && make k8s-up && make images TAG=dev && make deploy TAG=dev && make smoke && make netpol-test
```

`make seed`, `make eval` and `make replay` stop with a message until the ingestion job (issue #30) and the trace replay (P9-T01/T02) land.

## Result log

| Date | Machine / OS | Section | Result | Fixes made |
|---|---|---|---|---|
| 2026-11-27 | author's laptop, no Docker | A (script) | stopped at unit tests: the real-Redis budget cases needed Docker | marked them `integration` (PR #63) |
| | | | | |
