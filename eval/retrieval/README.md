# Retrieval evaluation (M6-06, CI gate)
- `queries.yaml`: 24 hand-written queries. Label `relevant: {card_id: grade}` after ingestion (Phase 3, task P3-T11).
- `test_retrieval.py` (to write in P3-T12): loads the committed snapshot into an ephemeral Qdrant/Postgres, runs every
  query in 4 modes, computes Recall@5/10, MRR@10, nDCG@10, writes `eval/results/retrieval-<date>.json`, and **fails if
  hybrid+rerank Recall@5 or MRR@10 < baseline − 0.02**.
- `baseline.json`: committed after the first labelled run; only changed by a reviewed PR with justification.
- Report: table of the 4 modes (ADR-002 uses it) + screenshot of the result.
