# 29 — Tech Stack (modern, professional, justified)

Principle: choose **current, widely-adopted, well-documented** tools that make each PDF requirement *easier to
prove*, not just possible. Every choice below names the requirement it serves. Pin exact versions in Phase 0
(latest stable at that time) in `uv.lock` / `package-lock.json` / image digests. Never use floating tags.

## 1. Python platform (all backend services, jobs)
| Concern | Choice | Why / requirement served |
|---|---|---|
| Runtime | **Python 3.12** (`python:3.12-slim` base, pinned by digest) | Matches Guide §4.4 Dockerfile pattern |
| Package/project manager | **uv** (workspace at repo root, one `pyproject.toml` per service, single `uv.lock`) | Fast, reproducible, lockfile → pinned versions (M14-08) |
| Web framework | **FastAPI** + **uvicorn** | Async, OpenAPI out of the box, Pydantic-native validation (M1-05, DF-01) |
| Data validation / schemas | **Pydantic v2** + **pydantic-settings** | Define schema once, validate every input/output, export JSON Schema (DF-01) |
| HTTP client | **httpx** (async) + **tenacity** | Timeouts + retries with jittered back-off (M3-03, NFR-03) |
| DB access | **SQLAlchemy 2.0** (async, typed) + **asyncpg** + **Alembic** | Repositories layer + migrations (M2-03, M2-06) |
| Lint / format | **ruff** (lint + format) | Guide uses `ruff check .` |
| Types | **mypy --strict** on `libs/` and `services/verdict`, normal elsewhere (pyright acceptable locally) | Guide uses `mypy services` |
| Tests | **pytest**, pytest-asyncio, **hypothesis** (property tests for M11), respx (httpx mocking), testcontainers (Postgres/Redis/Qdrant in integration tests), pytest-cov | M11-04, NFR-12 |
| Logging | **structlog** JSON to stdout | NFR-09 |
| Metrics | **prometheus-client** (`/metrics`) | G3, M13-07 |
| Tracing | **OpenTelemetry** SDK (GenAI semantic conventions for LLM spans) + our `TraceRecord` exporter → M13 | M13-01; OTel makes traces portable, our exporter keeps the Guide §2.3 schema |

## 2. Agentic AI layer
| Concern | Choice | Why |
|---|---|---|
| Orchestration (M3) | **LangGraph** (`StateGraph`) with **`langgraph-checkpoint-postgres`** | Explicit, testable state machines (M3-01); parallel fan-out via `Send` / parallel branches (M3-02); **`interrupt()` for human-in-the-loop** → clarifying questions (U5) and budget approval (U4) pause and resume exactly where they stopped; durable checkpoints so runs survive pod restarts; node-level caching helps "re-run only what changed" (M3-05). Graph is compiled once; every node is a plain async function (unit-testable without the graph). |
| Individual agents (M8 Ideator/Gap Analyst/Skeptic, M9 scorers, M12 critic) | **PydanticAI** agents with `output_type=<Pydantic model>` | Type-safe structured outputs, automatic validation + retry on invalid output, typed tool definitions, model-agnostic, `TestModel`/`FunctionModel` for free deterministic tests (NFR-12). Each agent = small focused agent with its own prompt file, tools, schema (avoids "one giant prompt", Guide §12). |
| LLM gateway (inside M5) | **Thin OpenAI-compatible adapter** (ADR-008), exposed as `POST /llm/v1/chat/completions`; LiteLLM can be plugged in behind the `Poster` protocol | One controlled exit (M5-01): provider calls use the same SSRF guard and pinned address as `/fetch`, which an SDK that does its own HTTP would bypass; no 35-package dependency tree or 700 MB image; prices in config so no model is free |
| Structured output from the small model | **llama.cpp server** with **JSON-schema / GBNF grammar-constrained decoding** | Raises valid-JSON rate toward 100% (M10-10); still validated by Pydantic and retried/fallbacked as required |
| Prompt management | Versioned Markdown files `prompts/<agent>@vN.md` with YAML front-matter (`id`, `version`, `model`, `output_schema`, `changelog`) + loader that hashes content | Prompt registry (M13-04), versions recorded on every verdict |
| Guardrails | Own M12 service: input rail (length/charset/regex + LLM-classifier optional), **spotlighting** (fetched text wrapped in `<evidence id=…>` delimiters, data-marking), sanitiser in M5, claim-evidence checker (NLI cross-encoder + exact id check) | M12-01..04, OWASP LLM Top-10 (LLM01 prompt injection) |
| Evaluation | Own harness (pytest + JSON reports) for verdict agreement/confusion matrix; retrieval metrics (Recall@k, MRR, nDCG) computed in-house; **optional** Ragas/DeepEval for extra RAG metrics | M13-05, M6-06. Keep core metrics dependency-free so CI is fast and deterministic |

**Why LangGraph + PydanticAI rather than one framework for everything:** LangGraph is the de-facto standard
for *stateful, controllable* multi-agent workflows with HITL and checkpointing, which is exactly M3. PydanticAI is the
lightest, most type-safe way to write *one agent that returns one validated object*, which is exactly M8/M9/M12.
Alternatives considered (record in ADR-007): CrewAI (less control over state/loops), AutoGen/AG2 (conversation-centric,
harder to make deterministic), OpenAI Agents SDK (provider-leaning), plain asyncio state machine (fine fallback if
LangGraph becomes a blocker; the node functions stay the same).

**Rules when using these frameworks**
- LangGraph nodes call other modules **over HTTP** (each module is its own container); never import another module's code.
- Graph state is a Pydantic model (`ValidateState`, `DiscoverState`); every node returns a partial update.
- `recursion_limit` and our own `round` counter both enforce the 3-round debate cap.
- Checkpointer = Postgres (schema `orchestrator`), thread_id = run_id → resume after `interrupt()`.
- PydanticAI agents always: `model_settings={"temperature": 0}` for scorers/critic, `retries=1`, explicit `output_type`,
  instrumented spans → TraceRecord.

## 3. Retrieval / data layer
| Concern | Choice | Why |
|---|---|---|
| Relational | **PostgreSQL 16** | App data + KB metadata (G2) |
| Vector store | **Qdrant** (StatefulSet + PVC) using **native hybrid search**: dense vectors + **sparse BM25/BM42** vectors in one collection, fused with RRF via the Query API, then cross-encoder rerank | Hybrid with reranking (M6-02) in one well-supported engine; payload filters for date/source/niche (M6-03) |
| Embeddings | **FastEmbed** (ONNX, CPU): dense `BAAI/bge-small-en-v1.5` (384-d), sparse `Qdrant/bm25` | No GPU needed, small images, models baked at build time (no runtime internet) |
| Reranker | FastEmbed cross-encoder (`Xenova/ms-marco-MiniLM-L-6-v2` or `BAAI/bge-reranker-base`) | Rerank top-50 → top-k |
| Keyword fallback | Postgres `tsvector` | Exact product-name matching, debugging |
| Clustering (M7) | **BERTopic-style** pipeline: UMAP (optional) + **HDBSCAN** + **c-TF-IDF** names (+ optional LLM label polish through M5) | Named niches (M7-01) |
| Trends | pandas / polars monthly aggregation + numpy linear regression slope | M7-02 |
| Dedupe | URL canonicalisation + sha256 content hash + MinHash (datasketch) near-duplicate detection | M6i-02 |
| Cache | **Redis 7** | M2-02 |

## 4. Fine-tuning (M10t) & serving (M10)
| Concern | Choice |
|---|---|
| Base model | **Qwen2.5-1.5B-Instruct** (primary; strong JSON following at small size) or **Llama-3.2-3B-Instruct** / Qwen3-1.7B if available (record choice in ADR) |
| Training | **Unsloth** (QLoRA 4-bit, 2× faster on free Colab T4/Kaggle P100) on top of **TRL `SFTTrainer`** + **PEFT**; experiment tracking with a CSV/JSON log committed (optional Weights & Biases free tier) |
| Export | merge adapter → **GGUF Q4_K_M** via Unsloth/llama.cpp convert |
| Serving | **llama.cpp `llama-server`** in its own container (+ small FastAPI wrapper exposing `/score`, `/health`, `/ready` and doing schema validation/retry/fallback) |
| Weights distribution | GitHub Release asset (or Hugging Face Hub private repo) → K8s **Job** "fine-tuned model import" downloads into a PVC **via M5** (allow-listed host) |

## 5. Frontend (M1 UI)
| Concern | Choice |
|---|---|
| Framework | **React 19 + TypeScript (strict) + Vite** |
| Routing / data | **TanStack Router** (or React Router 7) + **TanStack Query** (server state, retries, cache) |
| UI kit | **Tailwind CSS v4** + **shadcn/ui** (Radix primitives → accessible) + lucide icons + Recharts (compare/radar chart) |
| Forms | react-hook-form + **zod** (schemas mirrored from JSON Schema via `json-schema-to-zod` or hand-kept with a contract test) |
| Live stream | native `EventSource` (SSE) with reconnect + `Last-Event-ID` |
| Runtime config | `/config.js` generated at container start from env (`API_BASE_URL`) by an entrypoint script → **API address set at runtime** (M1-02) |
| Tests | **Vitest** + **@testing-library/react** + **MSW** (mock API) for component tests (M1-06); **Playwright** for 3–5 E2E smoke flows |
| Serving | multi-stage build → `nginxinc/nginx-unprivileged` (non-root) |
| Lint | ESLint (typescript-eslint) + Prettier |

## 6. Platform (M14)
| Concern | Choice |
|---|---|
| VCS / reviews | GitHub, protected `main`, rulesets, CODEOWNERS (both members) |
| CI | **GitHub Actions**: ruff, mypy, pytest, vitest, retrieval gate, schema-drift check, docker buildx (cache), **Trivy** image + config scan, **gitleaks**, **pip-audit**/`npm audit`, **Syft** SBOM, **kubeconform** + **kube-linter** manifests |
| CD | **Jenkins LTS** in Docker (JCasC optional), declarative `Jenkinsfile`, SCM polling `H/2 * * * *`, kubeconfig as Jenkins credential |
| Containers | Docker BuildKit multi-stage, non-root, pinned digests, `.dockerignore`, tags = short commit SHA, OCI labels |
| Registry | local `registry:2` connected to the kind network (`kind-registry:5000`) |
| Cluster | **kind** with `disableDefaultCNI: true` + **Calico** (NetworkPolicy enforcement) + ingress-nginx + metrics-server |
| Manifests | **Kustomize** (`k8s/base` + `k8s/overlays/dev`), ConfigMaps generated from `config/*.yaml` and `rubrics/` |
| Autoscaling | HPA (autoscaling/v2) on gateway, CPU 70% |
| Observability | kube-prometheus-stack (Prometheus + Grafana) *or* lightweight: Prometheus + Grafana Deployments with a provisioned dashboard JSON committed in `k8s/observability/` |
| Local dev | docker compose (dev only), Makefile/justfile, pre-commit hooks |

## 7. Security tooling (see 13_SECURITY.md)
gitleaks (pre-commit + CI), Trivy (images, IaC, fs), pip-audit, npm audit, Bandit (via ruff `S` rules), Syft SBOM,
kube-linter, Pod Security Admission `restricted` label on namespace, argon2-cffi, PyJWT, slowapi (rate limiting).

## 8. What we deliberately do NOT use (and why)
- Service mesh (Istio/Linkerd): too heavy for a laptop cluster in one week.
- Helm for our own charts: Kustomize is enough and simpler to review (Helm only for third-party charts).
- LLM-as-verdict anything: forbidden by design (ADR-001).
- Vector DB SaaS / hosted Postgres: must run in-cluster.
- Scraping frameworks (Scrapy/Playwright scraping): official APIs first (G5).
