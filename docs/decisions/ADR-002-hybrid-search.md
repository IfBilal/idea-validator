# ADR-002: Hybrid search (dense + sparse BM25 with RRF) with cross-encoder reranking
- **Status:** Proposed (fill numbers in Phase 3)
- **Context:** Vector search alone tends to miss exact product names; citations need precise chunks.
- **Options:** (1) vector only, (2) keyword only, (3) hybrid with reranking.
- **Decision:** Hybrid (Qdrant dense + sparse, RRF) + cross-encoder rerank of top-50.
- **Why:** Recall@5 vector = __, keyword = __, hybrid = __, hybrid+rerank = __ on our 24 queries (eval/results/retrieval-*.json).
- **Cost:** ~__ ms extra per query for reranking; sparse vectors in the same collection.
- **AI use:** Drafted by AI, options and numbers checked and changed by us.
