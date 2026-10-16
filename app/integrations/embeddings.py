"""Sentence similarity for the Verifier's Inferred route.

fastembed runs BAAI/bge-small-en-v1.5 on the CPU (ONNX, no torch). The model loads once in a
background thread at startup; the Docker image downloads it at build time. Until it is ready, or
if it can't load, a lexical similarity is used. Lexical scores run lower than embeddings, so the
fallback errs toward asking the user, never toward trusting a claim.
"""

import math
import re
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Protocol

import anyio
import structlog

logger = structlog.get_logger()


class Similarity(Protocol):
    @property
    def method(self) -> str: ...

    async def scores(self, pairs: Sequence[tuple[str, str]]) -> list[float]:
        """Cosine similarity (0..1) for each (a, b) pair."""
        ...


def _tokens(text: str) -> Counter[str]:
    return Counter(re.findall(r"[a-z0-9]+", text.lower()))


def lexical_similarity(a: str, b: str) -> float:
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    dot = sum(ta[t] * tb[t] for t in ta.keys() & tb.keys())
    return dot / (
        math.sqrt(sum(v * v for v in ta.values())) * math.sqrt(sum(v * v for v in tb.values()))
    )


class LexicalSimilarity:
    method = "lexical"

    async def scores(self, pairs: Sequence[tuple[str, str]]) -> list[float]:
        return [lexical_similarity(a, b) for a, b in pairs]


class EmbeddingSimilarity:
    """fastembed when loaded, lexical until then (or if loading failed)."""

    def __init__(self, model_name: str, cache_dir: Path | None) -> None:
        self.model_name = model_name
        self.cache_dir = cache_dir
        self._model: Any = None
        self._fallback = LexicalSimilarity()

    @property
    def method(self) -> str:
        return "embedding" if self._model is not None else "lexical"

    def _load_sync(self) -> Any:
        from fastembed import TextEmbedding  # heavy import, deferred

        return TextEmbedding(
            self.model_name, cache_dir=str(self.cache_dir) if self.cache_dir else None
        )

    async def load(self) -> None:
        try:
            self._model = await anyio.to_thread.run_sync(self._load_sync)
            logger.info("embeddings_loaded", model=self.model_name)
        except Exception as exc:  # the lexical fallback keeps the Verifier working
            logger.warning("embeddings_unavailable", error_type=type(exc).__name__)

    def _scores_sync(self, pairs: Sequence[tuple[str, str]]) -> list[float]:
        texts = list(dict.fromkeys(t for pair in pairs for t in pair))
        vectors = dict(zip(texts, self._model.embed(texts), strict=True))
        out: list[float] = []
        for a, b in pairs:
            va, vb = vectors[a], vectors[b]
            norm = float((va @ va) ** 0.5 * (vb @ vb) ** 0.5)
            out.append(float(va @ vb) / norm if norm else 0.0)
        return out

    async def scores(self, pairs: Sequence[tuple[str, str]]) -> list[float]:
        if not pairs:
            return []
        if self._model is None:
            return await self._fallback.scores(pairs)
        return await anyio.to_thread.run_sync(self._scores_sync, pairs)
