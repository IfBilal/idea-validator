# Report outline (PDF, ≤ 10 pages, not counting screenshots); Description §6.2
Budget pages roughly as shown. Every number must come from `eval/results/*` or measurement files.

1. **Summary and changes from the architecture** (0.75 p): what we built; deviations (ADR-003 LLM proxy in M5, ADR-004 learning scorer, M2 separate service, stack choices).
2. **Modules and architecture figure** (1.25 p): our version of Figure 1; one line per module; tech stack table.
3. **Verdict design** (2 p): gates, weights (+ justification of any change), thresholds, rubrics (one rubric excerpt), score blend, sensitivity, what-if; **tuning results with before and after confusion matrices** + false-GO count; **one worked example** (real idea from our system).
4. **Knowledge base** (1.25 p): sources + ethics (APIs, robots, rate limits), **card counts** per source/month, dedupe stats, niches; **retrieval results** table (4 modes, Recall@5/10, MRR, nDCG).
5. **Fine-tuning** (1.5 p): data (counts per dimension × level, hand-checked 60+, split by idea), settings (rank, lr, epochs, batch, loss curve), **comparison table** (base / fine-tuned / hosted: MAE, within-1, valid JSON, real ids, latency, cost), **one case where it did not help** + why.
6. **AgenticOps results** (1 p): tracing + replay, budgets & approvals stats, registry, eval harness behaviour checks, drift report, dashboard numbers (cost per verdict, latency per agent, cache hit rate, fallbacks, blocked injections).
7. **Platform** (1.5 p): GitHub flow, Actions checks, Jenkins pipeline, Kubernetes design (objects, NetworkPolicy, probes, HPA, rollout/rollback), **how we measured requests and limits** (method + table).
8. **Known limits and next steps** (0.5 p).
Appendix (not counted): screenshots with one-line captions (CHECKLIST.md order).
