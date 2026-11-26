# ADR-003: All hosted-LLM calls go through the M5 tool layer (LiteLLM proxy)
- **Status:** Accepted
- **Context:** NetworkPolicy must allow internet only for M5 (Description M5, Guide §9.3), but agents and M10's fallback need hosted models.
- **Options:** (1) give agent pods egress to LLM APIs; (2) a separate LLM-gateway pod with egress; (3) LLM proxy endpoint inside M5.
- **Decision:** (3) `POST /llm/v1/chat/completions` in M5 using LiteLLM, OpenAI-compatible so PydanticAI uses it via base_url.
- **Why:** keeps "only M5 reaches the internet" literally true; one place for keys, allow-list, cost accounting and budgets.
- **Cost:** M5 becomes a dependency of all agents (2 replicas, readiness), ~5–10 ms extra hop.
- **AI use:** Drafted with AI; reviewed by both members.
