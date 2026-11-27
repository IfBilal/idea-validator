# profile (M2): Profile, Session and Cache

Part of the Idea Validator system (docs/harness/03_ARCHITECTURE.md). One container per module.

| | |
|---|---|
| Module | M2 |
| Port | 8000 |
| Probes | `GET /health` (liveness), `GET /ready` (dependency checks), `GET /metrics` (Prometheus) |
| Owner | see `PROGRESS.md` / `services/README.md` |

## Layout
`src/svc_profile/`: `main.py` (app), `config.py` (settings), `api/` (routes only), `services/` (business logic),
`repositories/` (DB/Redis access), `clients/` (typed clients for other modules), `models/`.
Layering: `api -> services -> repositories/clients`; routes never touch the DB; services never import FastAPI.

## Config (environment / ConfigMap)
`LOG_LEVEL` (default INFO), `INTERNAL_TOKEN` (from a K8s Secret). Service-specific variables are added in `config.py`.
No hard-coded hosts: other modules are reached through `*_URL` environment variables.

## Idea versions for the orchestrator (challenge, what-if, Discover)
- `POST /internal/ideas` `{user_id,title,text}` creates the idea and version 1 (`{idea_id,version}`).
- `GET /internal/ideas/{id}/versions/{v}?user_id=` returns `idea_id, version, text, change_note, card` (and more); `card` is
  null until stored; a foreign or unknown idea or version is the same 404.
- `PUT /internal/ideas/{id}/versions/{v}` `{user_id,text,change_note,card,idempotency_key,base_version}` creates version
  `base_version+1` (`{v}` must equal it, else 422) and stores `card` verbatim (never altered; a null verdict stays null).
  Returns `{idea_id,version,created}`. Same key and content: `created:false`. 409 for a reused key with other content,
  a `base_version` that is not the latest, or a lost race (writers of one idea are serialised by a row lock).
  Limits: text 4000, change_note 500, card 256 KB, key `[A-Za-z0-9_.:-]{1,100}`; unknown fields are rejected.
- Migration `0002` adds nullable `card`, `idempotency_key` and a unique `(idea_id, idempotency_key)`.

## Run and test
```bash
uv run --package idea-validator-profile uvicorn svc_profile.main:app --port 8000
uv run pytest services/profile
docker build -f services/profile/Dockerfile -t profile:$(git rev-parse --short=7 HEAD) .
```
