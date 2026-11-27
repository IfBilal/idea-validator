"""FastEmbed-backed ``Models``: bge-small dense, BM25 sparse, MiniLM cross-encoder (ADR-002).

FastEmbed runs ONNX on CPU and is synchronous, so each call goes through a worker thread. Models are
loaded lazily on first use and cached on disk (``cache_dir``), so a pod downloads them once.
"""

import asyncio
from functools import cached_property

from fastembed import SparseTextEmbedding, TextEmbedding
from fastembed.rerank.cross_encoder import TextCrossEncoder

from svc_kb.services.search import SparseVec

DENSE_MODEL = "BAAI/bge-small-en-v1.5"
DENSE_DIM = 384
SPARSE_MODEL = "Qdrant/bm25"
RERANK_MODEL = "Xenova/ms-marco-MiniLM-L-6-v2"


class FastEmbedModels:
    def __init__(self, cache_dir: str | None = None) -> None:
        self._cache = cache_dir

    @cached_property
    def _dense(self) -> TextEmbedding:
        return TextEmbedding(DENSE_MODEL, cache_dir=self._cache)

    @cached_property
    def _sparse(self) -> SparseTextEmbedding:
        return SparseTextEmbedding(SPARSE_MODEL, cache_dir=self._cache)

    @cached_property
    def _rerank(self) -> TextCrossEncoder:
        return TextCrossEncoder(RERANK_MODEL, cache_dir=self._cache)

    async def dense(self, texts: list[str]) -> list[list[float]]:
        return await asyncio.to_thread(lambda: [v.tolist() for v in self._dense.embed(texts)])

    async def sparse(self, texts: list[str]) -> list[SparseVec]:
        def run() -> list[SparseVec]:
            return [
                SparseVec(indices=v.indices.tolist(), values=v.values.tolist())
                for v in self._sparse.embed(texts)
            ]

        return await asyncio.to_thread(run)

    async def rerank(self, query: str, texts: list[str]) -> list[float]:
        return await asyncio.to_thread(
            lambda: [float(s) for s in self._rerank.rerank(query, texts)]
        )
