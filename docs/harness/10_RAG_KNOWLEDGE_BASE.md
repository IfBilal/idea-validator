# 10 — Data Collection, Tool Layer, Knowledge Base (RAG), Niches & Trends
Covers M4 Scouts, M5 Tool Layer, M6i Ingestion, M6 Knowledge Base, M7 Niche & Trend Analyst.

## 1. Ethics & collection rules (Guide §5; these are graded)
- **Official APIs first.** Hacker News (Firebase API), GitHub (REST API), Reddit and Product Hunt (official APIs with
  keys). No HTML scraping where an API exists. For any site: read terms + `robots.txt` (record in `docs/data-sources.md`).
- **Respect limits**: per-domain rate limits, clear User-Agent (`IdeaValidatorBot/1.0 (+https://github.com/<org>/<repo>; student project)`),
  caching, exponential back-off on 429/5xx, honour `Retry-After`.
- **No logins, no private data.** Keep only what a card needs: name, public text, public URL. Drop usernames/emails/
  author handles (hash them only if needed for independence counting, never store raw).
- **Credit sources**: every card keeps `source` + `url`; verdict card links to them.
- **Fetched text is data**: may contain instructions aimed at models. Never obey (M12).
- **Snapshot** the card data (`data/snapshots/cards-<date>.jsonl.gz` + release asset) so the system works if a site is down.

## 2. Sources (`config/sources.yaml`); at least 3 required, plan for 4
| Source | API | What becomes a card | Target cards | Key needed |
|---|---|---|---|---|
| Hacker News | `https://hacker-news.firebaseio.com/v0/` (`showstories`, `askstories`, `item/{id}`) + Algolia HN Search API `hn.algolia.com/api/v1/search?tags=show_hn` for history | Show HN posts → product cards; Ask HN "is there a tool / I wish" → request/complaint cards; top comments → complaint chunks | 1,000+ | no |
| GitHub | `https://api.github.com/search/repositories?q=topic:<t> stars:>50` | repos → product/repo cards (name, description, topics, stars, created_at) | 500+ | token (free, raises rate limit), stored as Secret |
| Reddit | `https://oauth.reddit.com/r/{SideProject,startups,SaaS,EntrepreneurRideAlong}/top` (OAuth app) | posts → product/complaint/request cards | 300+ | client id/secret (Secret) |
| Product Hunt | `https://api.producthunt.com/v2/api/graphql` | launches → product cards (tagline, topics, votes) | 300+ | developer token (Secret) |
| YC directory | only if terms/robots allow; otherwise skip (it's optional in the figure) | company → product card (one_liner, batch, stage) | optional | — |
If Reddit/PH keys are not available in time, HN + GitHub + one of them still meets "≥ 3 sources". HN Show + Ask can
be counted as one source (hackernews). **Do not count sub-feeds as separate sources.**

## 3. M5 Tool Layer: the only exit to the internet
Pipeline for `POST /fetch`:
```
validate request → budget check (pages, bytes per run) → allow-list (exact host match, https only)
→ robots.txt (cached 24 h, urllib.robotparser, our UA; API hosts without robots → allowed by API terms)
→ per-domain token bucket (Redis; from sources.yaml, e.g. github 25/min, reddit 60/min, hn 30/s)
→ cache lookup (Redis, key sha256(url), TTL per source) → HTTP GET (httpx, timeout 10 s, max 2 MB, retries 2 w/ back-off)
→ sanitiser → cache store → trace record (tool_call, bytes, cache_hit, latency) → response
```
**Sanitiser** (unit-tested with a corpus of injection strings):
- HTML → text (selectolax/BeautifulSoup), drop `<script>/<style>/<iframe>`, comments, hidden elements, zero-width and
  bidi control characters, data URIs; normalise whitespace; truncate per field.
- Strip/neutralise instruction patterns aimed at models: "ignore (all|previous) instructions", "you are now", "system:",
  "assistant:", `<|im_start|>`, `[INST]`, "### instruction", "do not tell the user", markdown image exfil links
  `![](http…?q=)`, base64 blobs > 200 chars, "score this 5". Replace with `[removed: instruction-like text]`,
  set `flagged=true`, increment `blocked_injections_total{source}`.
- Never execute, never follow redirects to non-allow-listed hosts.
**LLM proxy** `POST /llm/v1/chat/completions` (LiteLLM): provider host allow-list, per-run token/cost budget, cost
returned and traced, model aliases (`hosted-default`, `hosted-cheap`) from ConfigMap, keys from Secret.
**NetworkPolicy**: only pods labelled `app: tools` get egress `0.0.0.0/0:443` (+ DNS). See 16_KUBERNETES.md.

## 4. M4 Scout agents
- One CLI + one container: `python -m scouts --source hn|github|reddit|producthunt|yc --since … --max-pages …`.
- **Scheduled**: CronJobs `scout-hn` (`0 */6 * * *`), `scout-github` (`30 */12 * * *`), `scout-reddit` (`15 */6 * * *`),
  `scout-ph` (`0 3 * * *`). `concurrencyPolicy: Forbid`, `backoffLimit: 2`, `activeDeadlineSeconds`.
- **On demand**: `POST /scout` (deployment) for Validate deep search, behind approval if over budget.
- **Failure handling per source**: retries with back-off; circuit breaker (3 consecutive failures → source marked
  `degraded` for 30 min, metric `scout_source_up{source}=0`); partial progress checkpoint (last id/cursor stored in
  `scout_state` table) so reruns continue; empty response handled (behaviour test "empty source").
- Writes `raw_pages` rows (sanitised body, url, source, fetched_at, content_hash). Never writes cards directly.

## 5. M6i Ingestion jobs (CronJob `ingestion`, `*/30 * * * *`, also runnable `python -m ingestion run`)
```
for raw in unprocessed raw_pages (batch 200):
   card = normalise(raw)             # source-specific mapper → IdeaCard schema (validated)
   canonical_url = canonicalise(url) # lowercase host, strip utm_*, trailing slash, fragments
   card.id = "card_" + sha1(canonical_url)[:8]   (collision → 12 chars)
   dedupe: exact (url, content_hash) → update last_seen; near-duplicate (MinHash Jaccard ≥ 0.9 on name+summary)
           → merge into existing card, add card_sources row (another platform = independence evidence)
   classify kind (product | complaint | request | discussion | repo) by source rules + keywords
   chunk text: chunk 0 = name + one_liner + problem + customer; then ~400-token chunks with 50 overlap;
               chunk_id = f"{card.id}#c{n}", payload carries card_id (MANDATORY), date, source, niche, kind
   embed (FastEmbed dense + sparse) in batches of 64 → upsert Qdrant (idempotent by point id)
   upsert Postgres cards, card_sources (ON CONFLICT DO UPDATE)
record ingestion_runs(run_id, source, added, updated, dropped, status)
bump kb index_version → retrieval cache invalidates
run freshness + coverage check → M13 drift report
```
**As built (P3-T07..T09, `jobs/ingestion/README.md`)**: M6 owns id minting, chunking (every chunk keeps `card_id`),
embedding and indexing, so the job sends *cards* to `POST /ingest` (batches of at most 50, token, 2 retries, resume
cursor) instead of chunks to Qdrant. Platforms are recorded by re-posting the same content once per extra platform
(M6 adds a `card_sources` row). `ingestion_runs` is a JSONL file in the job's state dir until M13 wants a table, and
`index_version` is not bumped by the job (M6 invalidates its own cache on ingest). `make seed` builds the ingestion
image, which carries `data/snapshots`, and runs the generated `seed` Job (`k8s/jobs/seed.yaml`, not in the base kustomization).

**Repeatable without duplicates** (M6i-02): running the job twice on the same raw pages leaves counts unchanged
(integration test). Target **≥ 1,500 cards from ≥ 3 sources** (aim 2,500 to leave room for dedupe).
Snapshot: `python -m ingestion snapshot` → `data/snapshots/cards-YYYYMMDD.jsonl.gz`; `python -m ingestion restore <file>`
(used by `make seed` and by the K8s seed Job on fresh clusters).

## 6. M6 Knowledge Base `POST /search`
```
query → (cache) → dense embed + sparse embed
→ Qdrant Query API: prefetch dense top-50 + sparse top-50 with payload filters (date range, sources, niches, kinds)
→ fusion RRF → top-50 → cross-encoder rerank → top-k (default 10)
→ map chunk → EvidenceItem {evidence_id=chunk_id, card_id, quote (best sentence ≤ 300 chars), source, url, retrieved_at}
→ trace (tool_call search_kb, k, filters, latency, cache_hit)
```
- `quote` is extracted sentence-level from the chunk (highest reranker score) so citations are precise.
- Freshness tracking: `GET /stats` returns card age histogram, `last_ingested_at` per source, % cards < 90 days.
- Coverage tracking: niches with < 10 cards or no card in last 90 days are flagged.

## 7. Retrieval quality (M6-06, CI gate)
- `eval/retrieval/queries.yaml`: **≥ 20 hand-written queries** (seed list provided, 24) each with `relevant_card_ids`
  labelled by humans after ingestion (grade 2 = highly relevant, 1 = relevant). Label by searching the KB/Postgres manually.
- Metrics: Recall@5, Recall@10, MRR@10, nDCG@10, for modes `vector`, `keyword`, `hybrid`, `hybrid+rerank` (ADR-002 table).
- `eval/retrieval/test_retrieval.py` (pytest) runs against the **snapshot** (deterministic) with a tiny local Qdrant
  (testcontainers or in-memory `:memory:` mode) and **fails if hybrid+rerank Recall@5 or MRR drops below
  `eval/retrieval/baseline.json` − 0.02**. Baseline updated only by a reviewed PR.

## 8. M7 Niche & Trend Analyst
- **Clustering** (CronJob after ingestion + `POST /recompute`): card chunk-0 embeddings → (UMAP 10-d optional) →
  HDBSCAN(min_cluster_size=15) → noise reassigned to nearest centroid if cosine ≥ 0.5 → c-TF-IDF top terms →
  name = top-3 terms (optionally polished by LLM via M5, cached) → `niches(id, name, size, trend_slope, status)`.
  Stable ids: match new clusters to old by centroid cosine ≥ 0.85 to keep niche ids across runs.
- **Monthly time series**: `niche_stats(niche_id, month, launches, mentions)` where launches = product cards published that
  month, mentions = complaint/request/discussion cards.
- **Status**: slope of mentions+launches over last 6 months via least squares, normalised by mean;
  `rising` slope ≥ +0.10, `saturated` = slope ≤ +0.02 AND size in top quartile AND launches/mentions ≥ 1.5, else `flat`.
- **Gaps** (feeds M8 and M9): for each niche, cluster complaint/request chunks; a gap = complaint cluster with
  ≥ 3 complaints from ≥ 2 sources whose embedding has no product card with cosine ≥ 0.75; `score = complaints ×
  log(1+sources) × recency`. Stored in `gaps` with evidence ids.
- `/niches/match`: profile interests + niche wish → niches ranked by (similarity, status bonus rising +0.2, saturated −0.2,
  feasibility proxy from typical scope).
- `/features`: see 09 §4.
