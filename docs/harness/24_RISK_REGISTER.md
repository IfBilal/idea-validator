# 24 — Risk Register

| # | Risk | Likelihood | Impact | Mitigation | Fallback / trigger |
|---|---|---|---|---|---|
| 1 | Laptop RAM can't run 13+ pods + Postgres + Qdrant + llama.cpp + Jenkins | High | High | Small requests (measured), 1 replica for non-critical services in dev overlay, kind with 2 workers, Jenkins outside kind, scorer Q4 GGUF ~1 GB | Merge M8+M9 into one "agents" Deployment (allowed by Guide 9.1, ADR); Grafana → `/dashboard`; run on the member's stronger laptop |
| 2 | Reddit / Product Hunt API keys delayed | Medium | Medium | Request on D1; HN + GitHub + one more | YC directory only if terms allow; otherwise any other official API (e.g. Lobsters JSON, Dev.to API) after reading terms |
| 3 | Not reaching 1,500 cards (rate limits) | Medium | High | Start scouting D2 AM, CronJobs overnight, Algolia HN history, GitHub topics list wide | Widen date range/topics; keep snapshot |
| 4 | Colab/Kaggle GPU unavailable or session dies | Medium | High | Unsloth (fast), checkpoint every N steps to Drive, Kaggle backup | Smaller model still in the 1B–3B range (Llama-3.2-1B-Instruct), fewer epochs, shorter max seq len |
| 5 | Small model invalid JSON | Medium | Medium | Grammar-constrained decoding, retry once, fallback hosted | Fallback is required behaviour anyway; report the rate |
| 6 | Hosted LLM cost overrun | Low | Medium | Budgets per run, cheap model alias, cache, recorded fixtures in CI | Lower budgets; scorers to M10 ASAP |
| 7 | NetworkPolicy not enforced | Medium | High | kind + Calico from day 1; test in P1 gate | k3d (k3s netpol controller) or minikube --cni=calico |
| 8 | Jenkins can't reach GitHub/cluster | Medium | Medium | SCM polling, Jenkins container on `kind` docker network with kubeconfig pointing to control-plane container | ngrok webhook; run Jenkins agent on host |
| 9 | LangGraph learning curve slows M3 | Medium | Medium | Nodes are plain functions; routing pure; follow official examples for interrupt + Postgres checkpointer | asyncio loop fallback (ADR) with same nodes |
| 10 | Verdict tuning overfits labelled set | Medium | Medium | k-fold, keep presets close to PDF, report false-GO separately | Keep PDF defaults, explain |
| 11 | Model or injected text influences verdict | Low | Critical | ADR-001, verdict-guardian review, tests asserting verdict == M11 output | Block merge |
| 12 | Secrets committed | Low | High | gitleaks pre-commit + CI, `.gitignore` | Rotate key immediately, purge history |
| 13 | Contribution imbalance in git history | Medium | Medium | Both author PRs daily, owners table, pair on shared tasks with alternating commits | Adjust before submission (honestly) |
| 14 | Last-day screenshot scramble | High | Medium | Capture screenshots at each gate (checklist) | Phase 11 buffer |
| 15 | Ambiguous requirement interpreted wrongly | Medium | Medium | `27_ASSUMPTIONS…` + ask instructor early | Document in report "changes from architecture" |
