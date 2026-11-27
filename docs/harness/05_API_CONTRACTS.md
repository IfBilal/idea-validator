# 05 — API Contracts

Names may differ if documented, but **every capability must exist** (Guide §3). Every service also exposes
`GET /health` (process alive → 200 `{"status":"ok"}`) and `GET /ready` (can serve now: DB/Redis/Qdrant/model
reachable → 200, else 503 with failing dependency). Every service exposes `GET /metrics` (Prometheus text).
All bodies are JSON validated against `schemas/*.schema.json` (Pydantic mirrors in `libs/common/schemas`).

## 0. Conventions
- Base path for the public API behind Ingress: `/api` (UI calls `${API_BASE}/...` where `API_BASE` comes from runtime `config.js`).
- Auth: `Authorization: Bearer <JWT>` on every public endpoint except `/auth/*`, `/health`, `/ready`.
- Internal endpoints require header `X-Internal-Token` and are not routed by Ingress.
- Error shape (all services):
  ```json
  {"error": {"code": "VALIDATION_ERROR|UNAUTHORIZED|FORBIDDEN|NOT_FOUND|RATE_LIMITED|BUDGET_EXCEEDED|UPSTREAM_TIMEOUT|UPSTREAM_ERROR|CONFLICT|INTERNAL",
             "message": "human readable", "details": {}, "request_id": "req_..."}}
  ```
- Status codes: 200/201/202, 400 validation, 401, 403, 404, 409, 413 payload too large, 422 schema, 429 (with `Retry-After`), 503 not ready, 504 upstream timeout.
- Pagination: `?limit=20&cursor=<opaque>` → `{"items": [...], "next_cursor": "..."}`.
- Limits: request body ≤ 32 KB (idea text ≤ 4,000 chars; profile arrays ≤ 30 items, each ≤ 50 chars).

---

## 1. Public endpoints (M1 Gateway)

### Auth
| Method/Path | Body | Response |
|---|---|---|
| `POST /auth/register` | `{"email","password"}` (password ≥ 10 chars) | 201 `{"user_id"}` |
| `POST /auth/login` | `{"email","password"}` | 200 `{"access_token","token_type":"bearer","expires_in":3600}`; 401 generic message; lockout after 5 failures for 15 min |

### Profile
| `GET /profile` | — | `Profile` (+ `version`) |
| `PUT /profile` | `Profile` without user_id | 200 `Profile` with incremented `version` |

`Profile` = Guide §2.1: `{user_id, skills[], hours_per_week, weeks_available, team_size, compute, goal, interests[], version}`.

### Runs
| Method/Path | Body | Response |
|---|---|---|
| `POST /discover` | `{"niche": "devtools" \| null, "count": 5 (1..10), "deep_search": false}` | 202 `{"run_id"}` |
| `POST /validate` | `{"idea_text": "...", "title": "...", "idea_id": null}` (idea_id when validating an existing/proposed idea) | 202 `{"run_id","idea_id","version"}` |
| `GET /runs/{id}` | — | `{"run_id","kind","state","progress":{...},"spent":{"cost_usd","tokens","pages","steps"},"budget":{...},"pending_question":{...}\|null,"pending_approval":{...}\|null,"result":{...}\|null}` |
| `GET /runs/{id}/events` | SSE (`text/event-stream`), supports `Last-Event-ID` | stream of `RunEvent` |
| `POST /runs/{id}/answer` | `{"question_id","answer"}` | 202 |
| `POST /runs/{id}/approve` | `{"approval_id","decision":"approve"\|"decline"}` | 202 |

Result for discover: `{"ideas":[{"idea_id","version","title","summary","niche","verdict","stability","score","reason","gap_evidence_ids":[]}], "dropped":[{"title","defeated_by_evidence_ids":[],"round"}]}` sorted by verdict rank, then score, then confidence. M3 adds `confidence`, `partial` and `needs_input` to each idea (a child
that stopped or failed has `verdict: null`, `partial: true`) and `idea_id`, `reason` to each dropped entry
(`fatal_objection | multiple_objections | no_gap_evidence`); the result also has `rounds`, `notes[]`, `degraded`, `partial`.
Result for challenge: `{"claim_id","outcome":"defended"|"removed","reason","evidence_ids","supporting_quote"?,"new_version"?,"card"?}`
(`card` is the new verdict card computed by M11). Result for what-if: the verdict card of the new version plus `base_version`,
`change_note` and `diff` (`verdict_from/to`, `verdict_changed`, `score_from/to`, per-dimension `from/to/delta`). A settled validate
card also has `changes[]` (concrete change texts from M8 for M11's `what_would_make_go` deltas) and `verdict_input` (the exact M11
request, used to recompute a challenge). Each `claims[]` entry has `text`, `evidence_ids`, `dimension`, `supported`, `removed`.
Result for validate: the **Verdict card** = M11 verdict JSON + `claims[]` + `challenge_log[]` + `cache_hit` + `evidence{id: EvidenceItem}` (for links).

**The verdict can be `null`.** M3 only shows a verdict M11 produced and the challenge settled. Otherwise the card is partial:
`verdict: null`, `partial: true`, none of the M11-derived fields (`stability`, `score`, `confidence`, `gates`, `weights`,
`thresholds`, `what_would_make_go`, `sensitivity`, `weakest_dimension`, `reasons`, `versions`), plus `notes[]`, `degraded`,
`needs_input[]` and, when relevant, `missing[]`, `stop_reason` (budget stop), `error` (failure) and the `scores` computed so far.
Causes: run `failed` or `budget_stopped` (including a stop between verdict and claim check, or a failed claim check or
recompute), or a required input the system refuses to guess (`needs_input: ["estimated_effort_hours"]`, run state `succeeded`).
**A card with `partial: true` has no verdict even when the run state is `succeeded`.** No `verdict` run event is emitted for it.
Rendering rule for the gateway/UI: if `verdict` is null, never show GO / PIVOT / NO-GO or a score. Show "needs your input"
(listing `needs_input`) or "stopped" (with `stop_reason` / `error` and the partial `scores`). The question run event and
`pending_question` carry `field`, `required` and `range` so the form can be rendered and validated client-side.

### Ideas
| `GET /ideas` | `?limit&cursor` | list `{idea_id,title,source,latest_version,latest_verdict,score,updated_at}` |
| `GET /ideas/{id}/versions` | — | list `{version,text,change_note,verdict,score,stability,created_at}` |
| `GET /ideas/{id}/versions/{v}` | — | full verdict card for that version |
| `GET /compare?a={idea_id}[:v]&b={idea_id}[:v]` | — | `{"a":{card summary},"b":{...},"dimensions":[{"dimension","a":{score,confidence,n_evidence},"b":{...},"delta"}], "verdicts":{"a","b"}}` |
| `POST /ideas/{id}/challenge` | `{"version", "claim_id"}` | 202 `{"run_id"}` → result `{"claim_id","outcome":"defended"\|"removed","reason","evidence_ids","new_version"?:int}` |
| `POST /ideas/{id}/whatif` | `{"version", "change_id"}` (apply one of the proposed changes) or `{"version","custom_change":"..."}` | 202 `{"run_id"}` → result = verdict card for `version+1` |

### Metrics
| `GET /metrics` | Prometheus text (or JSON) |

---

## 2. RunEvent (SSE) schema
```
event: <type>
id: <monotonic seq>
data: {"run_id","seq","ts","type", ...payload}
```
| type | payload |
|---|---|
| `run_started` | `kind, budget` |
| `state_changed` | `from, to` |
| `agent_started` / `agent_finished` | `agent, step_id, round?` |
| `tool_call` | `agent, tool, input_summary, latency_ms, cache_hit, error?` |
| `llm_call` | `agent, model, tokens_in, tokens_out, cost_usd, latency_ms, fallback?` |
| `spend_update` | `spent{cost_usd,tokens,pages,steps}, budget{...}` |
| `question` | `question_id, text, options?, why_it_matters` |
| `approval_required` | `approval_id, action, estimated_cost_usd, spent_usd, budget_usd, description` |
| `idea_dropped` | `title, round, defeated_by_evidence_ids` |
| `partial_result` | `idea_id, verdict, score` (Discover streams survivors as they finish) |
| `verdict` | full verdict card |
| `run_completed` / `run_failed` / `budget_stopped` | `status, reason?` |
| `heartbeat` | every 15 s |

---

## 3. Internal endpoints

### M2 Profile/Session/Cache (`/internal/*`)
`POST /internal/users` (`{email,password}` → 201 `{user_id}`; 409 duplicate email, 422 password < 10 chars), `POST /internal/auth/login` (`{email,password}` → `{user_id}`; one generic 401 for every failure; account locked 15 min after 5 failures; the gateway issues the JWT), `GET /internal/profiles/{user_id}[?version=]`, `PUT /internal/profiles/{user_id}` (each PUT adds a version), `POST /internal/ideas` (creates version 1), `GET /internal/ideas?user_id&limit&cursor` (newest first, keyset cursor),
`POST /internal/ideas/{id}/versions`, `GET /internal/ideas/{id}/versions[/{v}]?user_id` (the `/{v}` detail has `idea_id, version, text, change_note, card` plus title, clarifications and the stored verdict; `card` is null until a card is stored; a foreign or unknown idea/version is one identical 404), `PUT /internal/ideas/{id}/versions/{v}` (`{user_id,text,change_note,card,idempotency_key,base_version}` → `{idea_id,version,created}`; `{v}` must be `base_version+1` else 422; the card is stored verbatim, a `verdict: null` stays null; same key and same content returns `created:false`, same key with other content, a `base_version` that is not the latest, or a lost race gives 409; unknown or foreign idea 404; card ≤ 256 KB, text ≤ 4000, key `[A-Za-z0-9_.:-]{1,100}`, unknown fields 422), `POST /internal/verdicts` (body is the full `Verdict`; stores the payload and one row per dimension score),
`GET|PUT /internal/runs/{id}` (`?user_id` on GET; a run belongs to one user), `GET /internal/runs?state=a,b&limit=&after=` (M3 recovery: every user's runs in those states, at most 10 states of `[a-z_]{1,40}`, `limit` 1..500 default 100, paged by run id with `after`), `POST /internal/approvals`, `PUT /internal/approvals/{id}` (decided once, else 409),
`GET|PUT /internal/cache/verdict/{key}` (GET returns `{hit: bool, value}`; PUT 204 with `{value, ttl_s?}`, default TTL 24 h; build the key with `common.cache_keys.verdict_cache_key`), `GET /internal/compare?user_id&idea_ids=..` (2 to 5 ideas).
Every read takes the caller's `user_id`; someone else's idea, version or run is reported as 404, never 403.

### M3 Orchestrator
`POST /runs` `{kind, user_id, input}` → 202 `{run_id}`, `kind` is `validate | discover | challenge | whatif` and each `input` is
validated strictly (unknown fields, wrong types and out-of-range numbers are 422):
`validate` `{idea_text ≤4000, idea_id, version ≥1, title?, overrides?, unknowns?}`; `discover` `{niche? ≤200, count 1..10 (default 5),
deep_search?}`; `challenge` `{idea_id, version, claim_id}`; `whatif` `{idea_id, version, change_id | custom_change ≤1000}` (exactly one).
Ids are `[A-Za-z0-9_-]{1,64}` because they reach URL paths. Budgets come from the run kind in `config/budgets.yaml`.
`GET /runs/{id}`; `GET /runs/{id}/events?after=<seq>` (`{events:[RunEvent]}`, stored events with seq > after); `POST /runs/{id}/answer`; `POST /runs/{id}/approve` (only validate and what-if runs ask or need approval; 409 otherwise);
`POST /runs/{id}/cancel`; `POST /replay/{run_id}` (used by M13 replay).

### M4 Scouts
`POST /scout` `{source, query?, max_pages}` (on-demand deep search; runs only after approval if above budget) → `{fetched, raw_page_ids[]}`.
CLI: `python -m scouts --source hn|github|reddit|producthunt|yc [--since YYYY-MM-DD] [--max-pages N]`.

### M5 Tool Layer
`POST /fetch`
```json
in:  {"url": "https://hacker-news.firebaseio.com/v0/item/1.json", "source": "hackernews", "run_id": "run_90",
      "purpose": "scout|evidence", "max_bytes": 2000000}
out: {"url","final_url","status":200,"content_type","text":"<sanitised>","json":{...}|null,
      "cache_hit":false,"sanitiser":{"removed_patterns":2,"flagged":true},"fetched_at"}
errors: 403 DOMAIN_NOT_ALLOWED | 403 ROBOTS_DISALLOWED | 429 RATE_LIMITED | 402 BUDGET_EXCEEDED | 504 UPSTREAM_TIMEOUT
```
`POST /llm/v1/chat/completions` (OpenAI-compatible, LiteLLM; agents use it as `base_url`)
```json
in:  {"run_id","agent","model":"hosted-default","messages":[...],"max_tokens":800,"temperature":0,"response_format":"json"}
out: {"content","tokens_in","tokens_out","cost_usd","latency_ms","provider","model"}
```
(applies allow-list of provider hosts, budget check against `budget:{run_id}`, logs cost.)

### M6 Knowledge Base
`POST /search`
```json
in:  {"query":"slow export in note apps","k":10,"filters":{"date_from":"2025-01-01","date_to":null,
      "sources":["hackernews","reddit"],"niches":["productivity"],"kinds":["complaint","product"]},
      "mode":"hybrid","rerank":true,"run_id":"run_90"}
out: {"items":[EvidenceItem + {"score","rank","niche","published_at","card_name"}],"index_version":12,"cache_hit":false}
```
`GET /cards/{id}`, `GET /evidence/{evidence_id}` (→ EvidenceItem with full chunk text), `POST /evidence/batch` `{ids[]}` (existence + text; used by M12),
`GET /stats` (card counts by source/niche/month, freshness = age distribution, coverage = niches with < N recent cards).

### M7 Analyst
`GET /niches?status=rising|flat|saturated`, `GET /niches/{id}` (+ monthly series), `POST /niches/match` `{profile, niche_wish}` → ranked niches,
`GET /gaps?niche=` → gaps with evidence ids, `POST /features` `{idea_text, evidence_ids, profile}` → computed features (see 09 §4), `POST /recompute` (clustering job).

### M8 Ideation
`POST /ideate` `{run_id, profile, niches[], gaps[], round, previous_objections[]}` → `{ideas:[{title, summary, target_user, core_features[], gap_evidence_ids[], fit_reason}]}`
`POST /gap-analyst` `{run_id, ideas[]}` → ideas enriched with `complaints[]`, `missing_features[]` (+ evidence ids)
`POST /skeptic` `{run_id, ideas[] | idea + scores}` → `{objections:[{objection_id, idea_ref, dimension, claim, evidence_ids[]}]}`
`POST /suggest-changes` `{idea_text, target_dimension, current_score, required_delta, evidence}` → `{changes:[{change_id, text, target_dimension}]}`

### M9 Validators
`POST /score-all` `{run_id, idea, profile, evidence[], features}` → `{scores:[DimensionScore x6], abstained:[...], extra:{estimated_effort_hours, risk_blocker, duplicate_of}}`
(Runs the 4 scorers concurrently; each scorer also available as `POST /score/{scorer}`.)

### M10 Scoring Model
`POST /score`
```json
in:  {"rubric_id":"demand_pain@v3","idea":"...","evidence":[{"evidence_id","quote"}],"features":{...},"run_id"}
out: DimensionScore (score, confidence, evidence_ids, reason, rubric_version, model) + {"fallback": false, "retries": 0}
```

### M11 Verdict Engine
`POST /verdict`
```json
in:  {"idea_id","version","run_id","profile":{...},"scores":[DimensionScore x6],
      "gate_inputs":{"n_sources":4,"n_platforms":2,"estimated_effort_hours":180,"risk_blocker":null,
                     "duplicate_of":null},
      "objections_accepted":[{"dimension":"competition","objection_id":"o1"}],
      "config":{optional override for tuning/eval only; otherwise ConfigMap}}
out: Verdict (Guide §2.3 + extensions; see 08)
```
`POST /whatif` `{same input + "target":"GO"}` → `{"candidates":[{"dimension","from","to","delta","new_score","new_verdict","stable":true}],"profile_candidates":[{"field":"hours_per_week","from":5,"to":8,...}]}`
`POST /value-of-information` `{input, "unknown":"estimated_effort_hours","range":[80,300]}` → `{"verdict_changes": true, "verdicts":{...}}` (used for U5 clarifying questions)

### M12 Critic and Guardrails
`POST /check`
```json
in:  {"run_id","claims":[{"claim_id","text","evidence_ids":["card_8f21#c2"],"dimension":"demand_pain"}]}
out: {"results":[{"claim_id","supported":true|false,"reason":"evidence id missing|quote does not support|ok",
                   "supporting_quote":"...","checked_evidence_ids":[...]}]}
```
`POST /rail/input` `{text, kind:"idea|answer|niche"}` → `{allowed, reasons[], sanitized_text}`
`POST /approval/evaluate` `{run_id, action, estimated_cost_usd}` → `{needs_approval, spent, budget}`
`POST /objections/filter` `{objections[]}` → accepted (evidence-backed) vs discarded.

### M13 AgenticOps
`POST /trace` (TraceRecord or batch `[]`), `GET /runs/{id}/trace` (ordered records), `GET /runs?status=&kind=`,
`GET /dashboard` (HTML page reading metrics, if no Grafana), `GET /drift/latest`, `POST /eval/run` (starts Job), `GET /eval/results/{id}`,
`GET /registry` (prompt + rubric versions in use).
