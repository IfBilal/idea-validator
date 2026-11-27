# critic (M12): Critic and Guardrails

Part of the Idea Validator system (docs/harness/03_ARCHITECTURE.md). One container per module.

| | |
|---|---|
| Module | M12 |
| Port | 8000 |
| Probes | `GET /health`, `GET /ready` (KB reachable + `budgets.yaml` loadable), `GET /metrics` |

The critic only marks claims supported/unsupported with a reason. It never rewrites a claim and never
produces or alters a verdict (that is M11, plain code).

## Endpoints (all internal; need header `X-Internal-Token`)
| Endpoint | Purpose |
|---|---|
| `POST /check` | `schemas/claim_check.schema.json`. Per claim: (1) every evidence id must exist in M6 (`POST /evidence/batch`), else `evidence id missing`; (2) a competitor named in a competition-flavoured claim must match (fuzzy >= 0.9) the name of a cited KB card, else `competitor not in knowledge base: <name>`; (3) the `EntailmentJudge` (default: deterministic lexical overlap) must find support and return a quote that is a verbatim substring of the cited evidence (max 200 chars), else `quote does not support`. |
| `POST /objections/filter` | `{run_id, objections:[{objection_id, dimension?, text, evidence_ids}]}` -> `{accepted, rejected}`. Same rules as `/check`; at most `MAX_OBJECTIONS` accepted (rest rejected as `over objection cap`). M11 applies the -0.5 score effect, not this service. |
| `POST /rail/input` | `{text, kind: idea\|answer\|niche}` -> `{allowed, reasons[], sanitized_text}`. Rejects empty/over-long text, control and invisible/bidi characters, prompt injection (`common.injection`), harmful requests. Reasons are codes such as `too_long`, `invisible_characters`, `prompt_injection:delimiter_break`, `harmful_request:malware`. Input is never echoed or logged. |
| `POST /approval/evaluate` | `{run_id, kind, action, estimated_cost_usd, spent_usd}` -> `{decision: proceed\|needs_approval\|blocked, needs_approval, spent, budget, approval_threshold, projected, reason}`. `projected = spent + estimate`; `<= approval_threshold_usd` proceeds, up to `max(max_cost_usd x HARD_CAP_MULTIPLIER, threshold)` needs approval, beyond that is blocked. A zero-cost step always proceeds. |

The M6 `/evidence/batch` contract is `{ids[]}` -> `{items:[{evidence_id, card_id, card_name, text|quote, ...}]}`; ids
absent from `items` are treated as unknown.

## Config (environment / ConfigMap)
| Variable | Default | Meaning |
|---|---|---|
| `KB_URL` | unset (service not ready) | M6 base URL |
| `INTERNAL_TOKEN` | unset (all endpoints refuse) | shared internal token (K8s Secret) |
| `CONFIG_DIR` | `/etc/idea-validator` | directory holding `budgets.yaml` |
| `KB_TIMEOUT_S` / `KB_MAX_RETRIES` | 5 / 2 | outbound call limits |
| `JUDGE_THRESHOLD` | 0.5 | min share of claim content words found in one evidence sentence |
| `MAX_CLAIMS` / `MAX_OBJECTIONS` / `MAX_QUOTE_CHARS` | 50 / 5 / 200 | caps |
| `RAIL_MAX_IDEA_CHARS` / `RAIL_MAX_ANSWER_CHARS` / `RAIL_MAX_NICHE_CHARS` | 2000 / 1000 / 200 | rail length limits |
| `HARD_CAP_MULTIPLIER` | 2.0 | approval ceiling = `max_cost_usd` x this |
| `MAX_BODY_BYTES` | 262144 | request size guard |
| `LOG_LEVEL` | INFO | JSON logs on stdout |

An LLM judge can be added by implementing `EntailmentJudge` (`services/entailment.py`) on top of M5 `/llm` and
passing it to `build_app(judge=...)`; the checker re-verifies any quote it returns.

## Layout
`src/svc_critic/`: `main.py` (wiring), `config.py`, `api/` (routes), `services/` (claims, competitors, entailment,
objections, rail, approval), `clients/` (M6 client), `models/`. Layering `api -> services -> clients`; services never
import FastAPI (enforced by `tests/unit/test_critic_layering.py`).

## Run and test
```bash
uv run --package idea-validator-critic uvicorn svc_critic.main:app --port 8000
uv run pytest services/critic
docker build -f services/critic/Dockerfile -t critic:$(git rev-parse --short=7 HEAD) .
```
