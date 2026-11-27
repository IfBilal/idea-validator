# tools (M5): Tool Layer

The **only** way any module reads the internet (NetworkPolicy gives egress only to pods labelled
`app: tools`). Scouts, agents and ingestion call `POST /fetch`; hosted-LLM calls will go through
`POST /llm/v1/chat/completions` (LiteLLM, below).

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

## `POST /llm/v1/chat/completions`
The only way any agent reaches a hosted model (ADR-003). Body: `{run_id, agent, model: "hosted-default",
messages[1..20], max_tokens 1..4096, temperature 0..1 (default 0), response_format: "json"|"text"}`; unknown fields
are 422. Response: `{content, tokens_in, tokens_out, cost_usd, latency_ms, provider, model}`. Order of every call:

1. **Alias**: callers send an alias (`config/llm.yaml`), never a provider model name; unknown alias is 422.
2. **Provider host allow-list**: the alias host must be in `allowed_hosts` (checked at start-up and per call): 403 `PROVIDER_NOT_ALLOWED`.
3. **Budget, before any spend**: refuses with 402 `BUDGET_EXCEEDED` when the run's cost is used up or
   `spent tokens + max_tokens + estimated input` would exceed the token budget. The provider is not called.
4. **Call** (LiteLLM, `num_retries=0`, timeout 60 s): timeout is 504, any other provider failure is 502 with only the
   exception class name (provider text can hold keys or prompts).
5. **Charge** the real cost; when the provider cannot price the model, the alias `fallback_usd_per_1k_tokens` is charged, so no model is free.
6. **Trace**: one `llm_call` record (tokens, cost, model, latency, error). Prompts are not stored in it.

Known limits: the per-run ceiling is the largest budget (discover: 250k tokens, $0.25) until M3 opens runs with their
own limits in the Redis ledger (P2-T13); the ledger is in memory per replica until then. Concurrent calls of one run can
overshoot by (concurrency x one call), since a call's cost is only known afterwards. The image is about 700 MB (LiteLLM).

## Configuration
| Variable | Meaning |
|---|---|
| `REDIS_URL` | Secret; cache, robots cache and token buckets. The service refuses to start without it |
| `INTERNAL_TOKEN` | Secret shared with callers |
| `LLM_API_KEY` | Secret; provider key for `/llm`. The service refuses to start without it |
| `LLM_TIMEOUT_S`, `LLM_RUN_MAX_TOKENS`, `LLM_RUN_MAX_COST_USD` | provider timeout (60) and the per-run ceiling above |
| `CONFIG_DIR` | Directory with the mounted `sources.yaml` and `llm.yaml` (default `/etc/idea-validator`) |
| `RUN_MAX_PAGES`, `RUN_MAX_BYTES` | Per-run fetch budget (defaults 60 pages, 120 MB) |

## Known limits
- The sanitiser is a heuristic layer: obfuscations such as letter-spacing are not caught. Defence in depth is that
  fetched text is only ever shown to models as data, scorers never obey it, and the critic checks every claim.
- The run budget is in memory until the shared Redis ledger (`common.budget`) is wired in.
