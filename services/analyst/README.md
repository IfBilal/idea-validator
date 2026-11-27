# analyst (M7): Niche and Trend Analyst

Part of the Idea Validator system (docs/harness/03_ARCHITECTURE.md). One container per module.
Everything here is deterministic code: no model decides anything, no clock, no randomness.

| | |
|---|---|
| Module | M7 |
| Port | 8000 |
| Probes | `GET /health`, `GET /ready` (an analysis exists; retries the recompute), `GET /metrics` |
| Owner | see `PROGRESS.md` / `services/README.md` |

## Layout
`src/svc_analyst/`: `api/` (routes only), `services/` (`text`, `features`, `clustering`, `gaps`, `analyst`),
`clients/` (`kb.py` HTTP client for M6, `fakes.py` in-memory M6), `repositories/` (`memory.py` niche store),
`models/` (cards, domain, api, ports), `fixtures.py` (86 realistic cards, 6 niches, 5 platforms, 12+ months).
Layering: `api -> services -> clients/repositories`; services never import FastAPI (`test_analyst_layering.py`).

## Endpoints (internal, `X-Internal-Token` required; bodies are strict, errors use the standard envelope)
| | |
|---|---|
| `GET /niches?status=rising\|flat\|saturated` | niches (id, name, size, trend_slope, activity_slope, status, keywords) |
| `GET /niches/{id}` | one niche + monthly series (`month`, `launches`, `mentions`) + member card ids; 404 if unknown |
| `POST /niches/match` `{profile, niche_wish?, limit=5}` | niches ranked by `0.6*interest overlap + 0.2*skill overlap + status bonus` (rising +0.2, saturated -0.2); ties by id |
| `GET /gaps?niche=` | gaps `{id, niche_id, description, evidence_ids (>=1), complaint_count, product_coverage, score}` |
| `POST /features` `{idea_text (<=4000), evidence_ids (<=30), profile, required_skills?, scope?}` | flat dict of the 18 features of docs/09 section 4 + `required_skills` |
| `POST /recompute` | re-read M6, re-cluster, re-detect gaps; returns counts |

Env: `KB_URL` (M6), `KB_TIMEOUT_S`, `KB_MAX_RETRIES`, `KB_PAGE_SIZE`, `INTERNAL_TOKEN`, `RECOMPUTE_ON_START=true`,
`MAX_BODY_BYTES=65536`, `SEARCH_K=30`, `AS_OF` (ISO date; overrides the KB snapshot date), `RELEVANCE_TAU`, `DEMO_DATA=false` (run on the built-in fixture cards, dev only), `LOG_LEVEL`.
Without `KB_URL` (and without `DEMO_DATA`) the service answers 503 and `/ready` is false.

## Formulas (see the docstrings in `services/features.py`)
- `n_sources` distinct cards, `n_platforms` distinct platforms, `complaint_count` chunks of kind complaint/request,
  all over evidence with `score >= tau` (unscored evidence is kept; ids are de-duplicated).
- `recency_share`: share published on or after `as_of - 12 months` (inclusive; unknown date = not recent). `as_of` is the
  newest card date in the KB, never the wall clock.
- `trend_growth`: niche `trend_slope` = least-squares slope of monthly **mentions**, last 6 months, divided by their mean.
- `niche_saturation = (status + size percentile) / 2` (saturated 1, flat 0.5, rising 0; mid-rank percentile).
- `n_competitors`: product cards with `cosine(idea, card) >= 0.80` and the same target user (token overlap of the
  `for <audience>` phrase; an unknown audience on either side does not exclude). `competitor_strength`: mean of
  `ln(1+funding)/ln(1+1e9)`, stage weight and `ln(1+stars)/ln(1+1e5)`. `max_similarity` = max over products of
  `max(cosine, Jaccard of core-feature tokens)`.
- `unaddressed_gap_count`: gaps of the idea's niche with `cosine(idea, gap) >= 0.10` or sharing a cited evidence id.
- `skill_overlap = |skills & required| / |required|` (0.5 when no required skill is known); `available_hours = hours *
  weeks * team`; `estimated_effort_hours = base[S 60, M 160, L 400] * (1 + 0.5*(1 - overlap)) * 1.5 if GPU needed on a
  laptop`; scope from the text (`clauses + 2*complexity keywords`: <=3 S, <=8 M, else L) unless given.
- `compute_fit`: required (laptop < free_gpu < paid, from keywords) `<=` `profile.compute`.
- `risk_flags`: sorted keyword/regex flags (`health_data`, `minors`, `finance_trading`, `pii`, `scraping_logged_in`,
  `tos_dependent_api`); scraping behind a login also adds `blocker` (the risk rubric scores a blocker 0).
  `platform_dependency`: exactly one named third-party platform in the idea.
- `skill_novelty`: required skills that are in `profile.interests` or not in `profile.skills`.
  `cv_signal = 0.5 * share(required in in-demand list) + 0.5 * clamp(0.5 + trend_growth, 0, 1)`.

## Niches and gaps
`as_of` is never derived from the cards: it is the KB snapshot date (`GET /stats` -> `snapshot_date`, assumed) or `AS_OF`; cards dated after it are ignored and counted in `ignored_future`. Recompute runs in a worker thread, single flight (concurrent calls share one run), and startup recomputes in the background so probes answer at once.
Agglomerative (centroid linkage) clustering on TF-IDF cosine with an inverted-index candidate search (about 0.2 s for 1,500 cards, near-linear growth), merge threshold 0.15, minimum size 3, small clusters attach to
the nearest niche at cosine >= 0.05 or stay unassigned. Names are the top-3 c-TF-IDF terms; ids are `niche_` + hash of
those terms for new niches and are kept across runs by centroid cosine >= 0.85 (doc 10). Status uses the slope of mentions + launches (docs/10 section 8): a niche needs >= 3 months of data to be rising or saturated; `rising` >= +0.10; `saturated` = slope <=
+0.02 and size in the top quartile and launches/mentions >= 1.5; else `flat`. Gaps: a cluster of >= 3 complaints/requests
from >= 2 platforms that no product of the niche matches (cosine < 0.75); evidence ids are `<card_id>#c0`.
HDBSCAN + dense embeddings are the production option: plug an `Embedder` (`vectorize(texts)`) into `analyse`/`detect_gaps`.

## Assumed M6 shapes (not yet implemented in M6)
- `GET /cards?limit=&offset=` -> `{items: [Card], total}`; `POST /cards/batch {ids}` -> `{items: [Card]}`;
  `POST /search` as in docs/05 (extra fields ignored). A `Card` is the `idea_card` schema plus `platforms: [..]`
  (`card_sources.platform`) and optionally `github_stars`; unknown fields are ignored.
- M6 evidence ids are `<card_id>#c<n>`; M7 cites chunk 0 for whole-card evidence in gaps.

## Known limits (see docs/decisions/ADR-009-analyst-clustering.md)
- D4: not HDBSCAN; thresholds tuned on synthetic fixtures, cluster quality on real data is unmeasured.
- D6: only an in-memory `NicheStore` (no Postgres tables); state is lost on restart. Production shape: a recompute Job writes
  the tables and replicas only serve them.
- `POST /features` returns `rejected_evidence_count` for cited ids M6 does not know; optional `evidence_scores` plus
  `RELEVANCE_TAU` apply the rerank threshold.
- LLM polishing of niche names (would go through M5) is not done.
- No `import-linter` contracts (root `pyproject.toml` is not edited here); `test_analyst_layering.py` covers it.
- No k8s manifest yet.

## Run and test
```bash
uv run --package idea-validator-analyst uvicorn svc_analyst.main:app --port 8000   # DEMO_DATA=true to try it
uv run pytest services/analyst
docker build -f services/analyst/Dockerfile -t analyst:$(git rev-parse --short=7 HEAD) .
```
