# ADR-007: LangGraph for orchestration, PydanticAI for agents, LiteLLM as gateway
- **Status:** Accepted
- **Context:** M3 must be testable state machines with parallel agents, retries/timeouts, budget stops that ask the user, re-run only what changed. Agents need validated structured outputs.
- **Options:** (1) plain asyncio state machine; (2) LangGraph + PydanticAI + LiteLLM; (3) CrewAI; (4) AutoGen/AG2; (5) OpenAI Agents SDK.
- **Decision:** (2).
- **Why:** LangGraph gives explicit graphs, `interrupt()` HITL for questions/approvals, Postgres checkpointing (crash-safe resume), parallel branches. PydanticAI gives typed outputs with validation/retry and free deterministic test models. LiteLLM gives provider-agnostic calls with cost tracking inside M5.
- **Cost:** framework learning curve; pinned versions; fallback to (1) keeps the same node functions.
- **AI use:** Drafted with AI; reviewed by both members.
