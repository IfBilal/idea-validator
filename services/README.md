# services/: one deployable unit per module (own Dockerfile, image, Deployment)
| Dir | Module | Owner | Built in phase |
|---|---|---|---|
| gateway | M1 API Gateway | B | P1 stub → P2, P5 |
| ui | M1 Web UI (React) | B (+A) | P1 shell → P7 |
| profile | M2 Profile, Session & Cache | B | P2 |
| orchestrator | M3 Orchestrator (LangGraph) | B | P5, P6 |
| scouts | M4 Scout agents | A | P3 |
| tools | M5 Tool Layer (+ LLM proxy) | A | P2 |
| kb | M6 Knowledge Base | A | P3 |
| analyst | M7 Niche & Trend Analyst | A | P4 |
| ideation | M8 Idea Generator agents | B (+A skeptic) | P5, P6 |
| validators | M9 Validation agents | B | P5 |
| scorer | M10 Scoring Model (llama.cpp + wrapper) | A | P5 hosted mode → P8 |
| verdict | M11 Verdict Engine | B | P2 |
| critic | M12 Critic & Guardrails | A | P5 |
| agentops | M13 AgenticOps | A (+B eval) | P2 minimal → P9 |
Create each with `/new-service <name> <module>`.
