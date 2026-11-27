# tools (M5): Tool Layer

The **only** way any module reads the internet (NetworkPolicy gives egress only to pods labelled
`app: tools`). Scouts, agents and ingestion call `POST /fetch`; hosted-LLM calls will go through
`POST /llm/v1/chat/completions` (next PR, LiteLLM).

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

## Known limits
- The sanitiser is a heuristic layer: obfuscations such as letter-spacing are not caught. Defence in depth is that
  fetched text is only ever shown to models as data, scorers never obey it, and the critic checks every claim.
- The run budget is in memory until the shared Redis ledger (`common.budget`) is wired in.
