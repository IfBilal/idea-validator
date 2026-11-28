"""FastEmbed-backed ``Models``: bge-small dense, BM25 sparse, MiniLM cross-encoder (ADR-002).

FastEmbed runs ONNX on CPU and is synchronous, so each call goes through a worker thread. Models are
loaded lazily on first use and cached on disk (``cache_dir``), so a pod downloads them once.
"""

import asyncio
import shutil
from functools import cached_property
from pathlib import Path

from fastembed import SparseTextEmbedding, TextEmbedding
from fastembed.rerank.cross_encoder import TextCrossEncoder

from svc_kb.services.search import SparseVec

DENSE_MODEL = "BAAI/bge-small-en-v1.5"
DENSE_DIM = 384
SPARSE_MODEL = "Qdrant/bm25"
RERANK_MODEL = "Xenova/ms-marco-MiniLM-L-6-v2"


class FastEmbedModels:
    """``seed_dir`` holds the models baked into the image, read-only.

    The pod's root filesystem is read-only and the hub client writes lock files next to the models,
    so the first use copies the seed into the writable ``cache_dir`` (an emptyDir). No network.
    """

    def __init__(self, cache_dir: str | None = None, seed_dir: str | None = None) -> None:
        self._cache = cache_dir
        self._seed = seed_dir
        self.loaded = False

    def _prepare_cache(self) -> None:
        if not (self._seed and self._cache):
            return
        target = Path(self._cache)
        if target.exists() and any(target.iterdir()):
            return
        shutil.copytree(self._seed, target, dirs_exist_ok=True)

    @cached_property
    def _dense(self) -> TextEmbedding:
        return TextEmbedding(DENSE_MODEL, cache_dir=self._cache)

    @cached_property
    def _sparse(self) -> SparseTextEmbedding:
        return SparseTextEmbedding(SPARSE_MODEL, cache_dir=self._cache)

    @cached_property
    def _rerank(self) -> TextCrossEncoder:
        return TextCrossEncoder(RERANK_MODEL, cache_dir=self._cache)

    def warm(self) -> None:
        """Load all three models now (blocking: run it in a thread) so request one is not slow."""
        self._prepare_cache()
        _ = (self._dense, self._sparse, self._rerank)
        self.loaded = True

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
