# M8 Ideation (`svc_ideation`)

Five small, single-purpose agents for the Discover flow. Each renders a versioned prompt from
`prompts/<agent>@v1.md`, calls M5 `/llm`, validates the reply and enforces the rules in code.
The model never outputs or changes a verdict: unknown keys (`verdict`, `score`, `decision`, ...) are
discarded by the parsers. Debate rounds are driven by M3; `/ideate` rejects `round` > 3.

## Endpoints (internal, `X-Internal-Token`)

| Endpoint | Agent | Notes |
|---|---|---|
| `POST /ideate` | Ideator | Drops ideas with unknown gap, invented evidence ids, effort above available hours (S 60 / M 160 / L 400 h vs hours x weeks x team), or Jaccard feature overlap >= `SIMILARITY_THRESHOLD` with an M6 product. One JSON retry, then `ideas: []` with `reason`. |
| `POST /gap-analyst` | Gap Analyst | Complaints / missing features, each with >= 1 evidence id that was shown to the model; max 4 per list. |
| `POST /skeptic` | Skeptic | Objections per idea and dimension, evidence ids must exist in M6; max 3 per idea (most severe kept). |
| `POST /suggest-changes` | Change Suggester | Input is M11's delta (`target_dimension`, `current_score`/`from_score`, `required_delta` or `to_score`); text only. |
| `POST /rewrite` | Idea Rewriter | New text <= 4000 chars, not identical to the input; otherwise 502 `UPSTREAM_ERROR`. |

Errors use the standard envelope. Model garbage never becomes a 500.

## Environment

`TOOLS_URL` (M5), `KB_URL` (M6), `ANALYST_URL` (M7, optional), `INTERNAL_TOKEN`, `PROMPTS_DIR`
(default `/app/prompts`), `LLM_MODEL` (`hosted-default`), `LLM_TIMEOUT_S` (60), `KB_TIMEOUT_S` (5),
`MAX_RETRIES` (2), `LLM_MAX_TOKENS`, `MAX_IDEAS`, `SIMILARITY_THRESHOLD` (0.6), `SEARCH_K`,
`MAX_EVIDENCE_ITEMS`, `MAX_OBJECTIONS_PER_IDEA` (3), `MAX_ITEMS_PER_IDEA` (4), `MAX_CHANGES` (3).

## Assumed upstream shapes

- M5 `POST /llm/v1/chat/completions` `{run_id, agent, model, messages, max_tokens, temperature,
  response_format}` -> `choices[0].message.content` (+ `usage`) or flat `{content, tokens_in, ...}`.
- M6 `POST /search` `{query, k, filters{kinds}, mode, rerank, run_id}` -> `{items:[EvidenceItem]}`;
  for `kinds:["product"]` the item `quote` is the product's feature text, `card_name` its name.
  `POST /evidence/batch` `{ids}` -> `{items:[EvidenceItem]}` (missing ids simply absent).
- M7 `GET /gaps?niche=` -> `{gaps:[{gap_id, text, evidence_ids[]}]}` (optional, failures are logged).

Evidence is HTML-escaped and wrapped in `<evidence id=.. source=.. date=..>`; other embedded text goes
through `safe_json`, which escapes `< > &`.
