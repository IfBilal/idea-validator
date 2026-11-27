# ADR-008: M5 LLM proxy talks to providers through a small OpenAI-compatible adapter (LiteLLM stays pluggable)
- **Status:** Accepted
- **Context:** ADR-003 puts every hosted-model call in M5 and names LiteLLM. The M5 image must stay small, every call must pass the same SSRF guard and pinned-address path as `/fetch`, and per-run cost accounting must be exact and testable offline.
- **Options:** (1) `litellm` SDK inside M5; (2) a thin adapter that POSTs OpenAI-compatible chat requests (Groq, OpenAI and most providers speak it) through the existing pinned fetcher; (3) separate gateway pod.
- **Decision:** (2). `services/llm.py` owns alias resolution (`config/llm.yaml`), key lookup from the environment, retries, cost arithmetic (Decimal) and per-run caps. The provider call sits behind the `Poster` protocol, so a LiteLLM-backed implementation can replace it without touching callers.
- **Why:** no extra dependency tree (litellm pulls dozens of packages and its own HTTP stack that would bypass the URL policy and address pinning), exact control over what is logged (never prompts, completions or keys), and every code path is unit-testable with fakes.
- **Cost:** only OpenAI-compatible providers are supported until another adapter is written (Anthropic native API, Google Gemini non-compat). Acceptable: Groq and OpenAI cover the project.
- **AI use:** Drafted with AI; reviewed by both members.
