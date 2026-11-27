# tools (M5): Tool Layer

The **only** way any module reads the internet (NetworkPolicy gives egress only to pods labelled
`app: tools`). Scouts, agents and ingestion call `POST /fetch`; hosted-LLM calls will go through
`POST /llm/v1/chat/completions` (OpenAI-compatible adapter, ADR-008).

| | |
|---|---|
| Module | M5, port 8000 |
| Probes | `GET /health`, `GET /ready` (Redis), `GET /metrics` |
| Auth | internal endpoints need `X-Internal-Token` |

## `POST /fetch`
Body per `schemas/fetch.schema.json`: `{url, source, purpose: scout|evidence|model_import, run_id?, max_bytes?}`
(`max_bytes` up to 5,000,000; default 2,000,000). Pipeline, in order:

1. **URL policy** (`url_policy.py`): https only, port 443, no credentials, host must match the allow-list in
   `config/sources.yaml` **exactly**, IP literals never accepted, and every address the host resolves to must be a
   routable public unicast address (private, loopback, link-local, metadata, CGNAT, multicast, reserved and
   IPv4-mapped/NAT64 forms are refused). The connection goes to the checked address (no DNS rebinding).
2. **Cache** (Redis, `fetch:{sha256(url)}`, TTL per source). Cache hits cost no budget and no rate-limit token.
3. **Run budget**: pages and bytes per `run_id` (402 `BUDGET_EXCEEDED`).
4. **robots.txt**: cached 24 h; 4xx means no rules, 5xx or errors mean disallow; official API hosts listed in
   `robots_exempt` follow their API terms instead.
5. **Per-domain token bucket** (Redis Lua, atomic): burst up to the per-minute rate (429 with `retry_after`).
6. **GET**: 10 s timeout, size cap, 2 retries with back-off on 5xx/timeouts, no cookies or credentials,
   redirects never followed automatically: each hop (max 3) goes through the URL policy again.
7. **Sanitiser** (`sanitiser.py`): HTML to visible text, hidden elements and invisible characters dropped,
   instruction-like phrases, exfiltration links, base64 blobs and data URIs replaced by
   `[removed: instruction-like text]`; JSON string values are sanitised too (keys are not).

Refusals are 403 with `error.details.reason`: `DOMAIN_NOT_ALLOWED`, `INVALID_URL`, `BLOCKED_ADDRESS`,
`ROBOTS_DISALLOWED`, `SOURCE_DISABLED`. Counters: `blocked_injections_total{source}`, `fetch_requests_total{source,outcome}`.

## Configuration
| Variable | Meaning |
|---|---|
| `REDIS_URL` | Secret; cache, robots cache and token buckets. The service refuses to start without it |
| `INTERNAL_TOKEN` | Secret shared with callers |
| `CONFIG_DIR` | Directory with the mounted `sources.yaml` (default `/etc/idea-validator`) |
| `RUN_MAX_PAGES`, `RUN_MAX_BYTES` | Per-run fetch budget (defaults 60 pages, 120 MB) |

## Bounds on hostile input (measured, round 2)
- Normalisation is done in chunks and stops at the cap: one character can expand 18x under NFKC (U+FDFA), and a 2.1 MB body
  of them took 30 s before; it is about 1.6 s now.
- Sanitising runs in a worker thread (`asyncio.to_thread`), so a heavy page no longer freezes health probes or other requests.
- A JSON body with more than 20,000 strings plus keys is sanitised as capped plain text instead of structured JSON.
- One overall deadline (30 s) covers retries and redirects together (`UpstreamTimeout`).
- A redirect hop is checked like a request: the destination host's source must be `enabled`, and the rate limit of the
  destination host (not the first one) applies.
- Not done: a cache hit still returns the stored body even when the caller asked for a smaller `max_bytes`.

## Known limits
- The sanitiser is a heuristic layer: obfuscations such as letter-spacing are not caught. Defence in depth is that
  fetched text is only ever shown to models as data, scorers never obey it, and the critic checks every claim.
- The run budget is in memory until the shared Redis ledger (`common.budget`) is wired in.

## `POST /llm/v1/chat/completions`
Body: `{run_id, agent, model (alias), messages[1..50], max_tokens 1..4096 (800), temperature 0..2 (0), response_format json|text}`.
Reply: `{content, tokens_in, tokens_out, cost_usd, latency_ms, provider, model}` plus OpenAI-shaped `choices` and `usage`.
- **Aliases** live in `config/llm.yaml` (`hosted-default`, `hosted-cheap`): provider host (must be on the allow-list, checked at start-up), model, price per million tokens and `api_key_env`. **Keys come from environment variables (K8s Secret), never from files or git**, and are sent only to that provider.
- **Same network guard as `/fetch`:** URL policy, SSRF check and pinned address; compressed replies refused; 2 MB reply cap; retries (2) on 5xx and timeouts only; 429 is passed on with `retry_after`; provider 4xx become a generic 502 that never echoes the provider's body.
- **Budget:** per run (`LLM_RUN_MAX_TOKENS` 250000, `LLM_RUN_MAX_COST_USD` 0.25, the largest values in `config/budgets.yaml`; M3 enforces the exact per-kind numbers). A pre-flight check refuses a call whose worst case would cross the cap (402 `BUDGET_EXCEEDED`), actual cost is charged exactly (Decimal), and a run that went over is stopped.
- **Never logged:** prompts, completions, keys. Metrics: `llm_tokens_total{alias,direction}`, `llm_cost_usd_total{alias}`.
