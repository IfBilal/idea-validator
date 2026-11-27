# 04 — Data Model

Reference: `guide_fig1_data_model_from_pdf.png`. Field lists are **minimums**; we add fields where needed
(marked ➕). Arrows in the figure: users → profiles → ideas → idea_versions → verdicts → dimension_scores;
cards → card_sources; cards → vector store (chunks link to cards); cards link to niches; every claim links to card ids.
**Redis only holds cache entries.**

## 1. Postgres: application data (owned by M2, schema `app`)

```sql
-- users
id UUID PK, email CITEXT UNIQUE NOT NULL, hash TEXT NOT NULL (argon2id), created_at TIMESTAMPTZ NOT NULL
➕ last_login_at TIMESTAMPTZ, ➕ failed_logins INT DEFAULT 0, ➕ locked_until TIMESTAMPTZ

-- profiles  (one current row per user + history)
user_id UUID FK users, skills TEXT[], hours_per_week INT CHECK (0..80), weeks_available INT CHECK (1..52),
team_size INT CHECK (1..10), compute TEXT CHECK IN ('laptop','free_gpu','paid'),
goal TEXT CHECK IN ('startup','portfolio','course_project'), interests TEXT[]
➕ version INT NOT NULL (increments on every PUT), ➕ updated_at, PK (user_id, version)
-- current profile = max(version). Old versions kept so old verdicts stay explainable.

-- ideas
id UUID PK, user_id FK, title TEXT, source TEXT CHECK IN ('own','generated')
➕ created_at, ➕ niche_id (nullable), ➕ origin_run_id (Discover run that generated it)

-- idea_versions
idea_id FK, version INT, text TEXT, created_at
➕ change_note TEXT (e.g. "what-if: narrow to X segment" / "challenge removed claim c3"), ➕ parent_version INT,
➕ clarifications JSONB (answers to clarifying questions), PK (idea_id, version)

-- verdicts
id UUID PK ➕, version_id → (idea_id, version), verdict TEXT CHECK IN ('GO','PIVOT','NO-GO','INSUFFICIENT_EVIDENCE'),
score NUMERIC(5,2) NULL, confidence NUMERIC(4,3)
➕ run_id, ➕ stability TEXT, ➕ profile_version INT, ➕ payload JSONB (full verdict JSON from M11, Guide §2.3),
➕ engine_version, rubrics_version, weights_version, ➕ created_at

-- dimension_scores
verdict_id FK, dimension TEXT CHECK IN (six), score NUMERIC(3,1) NULL (NULL = abstained), conf NUMERIC(4,3),
evidence TEXT[] (evidence ids) ➕ features JSONB, reason TEXT, rubric_version, model, adjusted_by_challenge NUMERIC(3,1) DEFAULT 0

-- approvals
run_id, action TEXT (e.g. 'deep_search'), decision TEXT CHECK IN ('pending','approved','declined','expired')
➕ estimated_cost_usd, budget_usd, spent_usd, requested_at, decided_at, decided_by

-- traces_index   (written by M13; Postgres schema `ops` is acceptable)
run_id PK, cost NUMERIC(10,5), latency INT (ms), status TEXT CHECK IN ('running','awaiting_input','awaiting_approval','succeeded','failed','cancelled','budget_stopped')
➕ kind ('discover'|'validate'|'whatif'|'challenge'|'eval'), user_id, idea_id, started_at, ended_at, steps INT,
tokens_in, tokens_out, pages, cache_hits, fallbacks, trace_path TEXT

-- conversations
idea_id FK, turns JSONB  (list of {role, content, ts, run_id})
➕ id, user_id

➕ -- runs (state machine persistence for M3; could live in traces_index but kept separate for clarity)
run_id PK, user_id, kind, state TEXT, input JSONB, context JSONB (memoised step outputs keyed by step+input_hash),
pending_question JSONB, created_at, updated_at
```

Migrations: Alembic in `services/profile/migrations/`. Every schema change = new migration (never edit an applied one).
Indexes: `ideas(user_id, created_at desc)`, `verdicts(version_id)`, `dimension_scores(verdict_id)`, `runs(user_id, created_at)`.

## 2. Knowledge base (M6, M7; Postgres schema `kb` + Qdrant)

```sql
-- cards (Postgres)
id TEXT PK ('card_' + 8 hex of sha1(canonical_url)), name, one_liner (= summary), problem, customer, niche (primary niche id)
➕ niche_tags TEXT[], stage TEXT ('launched'|'seed'|'series_a'|...|'unknown'), funding_usd BIGINT NULL,
➕ source TEXT (primary platform), url TEXT UNIQUE, published_at DATE, fetched_at DATE, text TEXT (cleaned),
➕ content_hash TEXT (sha256 of normalised text, for dedupe), ➕ kind TEXT ('product'|'complaint'|'request'|'discussion'|'repo'),
➕ tsv TSVECTOR (generated, for keyword search), ➕ first_seen_at, last_seen_at

-- card_sources   (one card can be seen on several platforms → independence counting)
card_id FK, platform TEXT ('hackernews'|'github'|'lobsters'|'stackexchange'|'reddit'|'producthunt'|'yc'; ADR-009: the first four need no credential), url TEXT, fetched_at
PK (card_id, url)

-- niches
id TEXT PK, name TEXT, size INT, trend_slope NUMERIC ➕ status ('rising'|'flat'|'saturated'), ➕ keywords TEXT[], ➕ updated_at

-- niche_stats
niche_id FK, month DATE (first of month), launches INT, mentions INT  PK (niche_id, month)

➕ -- gaps (output of M7, input to M8 and M9)
id TEXT PK, niche_id, description TEXT, evidence_ids TEXT[], complaint_count INT, product_coverage INT, score NUMERIC, created_at

-- ingestion_runs
run_id PK, source TEXT, added INT, dropped INT ➕ updated INT, started_at, ended_at, status, error TEXT

➕ -- raw_pages (scout output; input to M6i)
id, source, url, fetched_at, status_code, body JSONB/TEXT (sanitised), content_hash, processed BOOL
```

### Vector store (Qdrant collection `chunks`)
| field | meaning |
|---|---|
| `chunk_id` (point id) | `"<card_id>#c<n>"` (also the **evidence_id**) |
| vector | 384-d bge-small embedding, cosine |
| payload.card_id | **mandatory** (Guide §12: never split text without keeping the card id) |
| payload.date | published_at (filterable) |
| payload.source, payload.niche, payload.kind | filterable |
| payload.text | chunk text (≤ 512 tokens, 64 overlap) |

Evidence id format: `card_8f21#c2` = chunk 2 of card `card_8f21`. `#c0` = name + one_liner + problem summary chunk.

## 3. Redis (cache only, every key has TTL)
| Key | Value | TTL |
|---|---|---|
| `verdict:{idea_id}:{idea_version}:{idea_hash}:p{profile_version}:r{rubrics_version}:w{weights_version}:e{engine_version}` | verdict JSON | 24 h |
| `retrieval:{sha256(query+filters)}:kb{index_version}` | ranked evidence list | 6 h |
| `score:{dimension}:{sha256(idea_text+evidence_ids+features)}:r{rubric_version}:m{model}` | dimension score | 24 h |
| `fetch:{sha256(url)}` | sanitised fetch result (M5) | 6 h (per source config) |
| `robots:{domain}` | parsed robots.txt | 24 h |
| `ratelimit:{domain}` / `ratelimit:user:{id}` / `ratelimit:ip:{ip}` | token bucket | window |
| `budget:{run_id}` | hash {tokens, pages, steps, cost} | 7 d |
| `run:{run_id}` (pub/sub channel) | run events | n/a |

`index_version` increments after each ingestion run so stale retrieval results are not served after new data.
Cache hits: every read reports `cache_hit=true` in the trace and increments `cache_hits_total{cache=...}`; the
verdict card shows a "cached" badge (M2-05).

## 4. Files on volumes / in git
| Path | Where | Content |
|---|---|---|
| `traces/*.jsonl` | PVC `traces` (M13) | one file per run: `traces/<run_id>.jsonl` |
| `prompts/` | git (mounted as ConfigMap or baked) | `<agent>@v<N>.md` |
| `rubrics/` | git (ConfigMap) | `<dimension>@v<N>.yaml` |
| `eval/results/*.json` | PVC `eval` + committed summaries | eval harness outputs (confusion matrices, behaviour checks) |
| `data/snapshots/cards-<date>.jsonl.gz` | git (if < 20 MB) or GitHub release asset | card snapshot for offline start |
| `models/*.gguf` | PVC `models` (imported by Job from release asset) | fine-tuned scorer weights, never in git |

## 5. Versions everywhere
| Version | Source | Recorded in |
|---|---|---|
| `profile_version` | profiles.version | verdict, cache key |
| `idea_version` | idea_versions.version | verdict, cache key |
| `rubrics_version` (`r5`) | hash/label of rubric set, from `config/verdict.yaml: rubric_set` | verdict.versions.rubrics, dimension_scores.rubric_version |
| `weights_version` (`w2`) | `config/verdict.yaml: weights_version` | verdict.versions.profile_weights |
| `engine_version` (`m11@v1`) | M11 package version | verdict.versions.engine |
| `prompt_version` | `prompts/<agent>@vN` | trace records |
| `model` (`scorer-lora@v2`) | M10 model tag | dimension score, trace |
| `index_version` | KB, incremented per ingestion | retrieval cache key, drift report |
