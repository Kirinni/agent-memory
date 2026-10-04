"""Optional cross-encoder reranking: a second stage that rescores the top candidates.

Same optional dependency as the dense leg (`fastembed`). When the model cannot be loaded
the recall keeps its fused order and reports why; a failed load is remembered, so a
missing model costs one attempt per process, not one per query.
"""

from __future__ import annotations

import threading
from collections.abc import Iterable
from typing import Protocol

INSTALL_HINT = "pip install 'agent-memory-core[vector]'"


class RerankerUnavailable(RuntimeError):
    """The optional reranking stage cannot run; callers fall back to the fused order."""


class CrossEncoder(Protocol):
    def rerank(self, query: str, documents: Iterable[str]) -> Iterable[float]: ...


_encoders: dict[str, CrossEncoder] = {}
_failures: dict[str, str] = {}
_lock = threading.Lock()


def load(model: str) -> CrossEncoder:
    """The model, built once per process and remembered; failures are remembered too."""
    with _lock:
        if model in _encoders:
            return _encoders[model]
        if model in _failures:
            raise RerankerUnavailable(_failures[model])
        try:
            from fastembed.rerank.cross_encoder import TextCrossEncoder
        except ImportError as error:
            _failures[model] = (
                "reranking is enabled but the optional dependency is unavailable; "
                f"install it with: {INSTALL_HINT}"
            )
            raise RerankerUnavailable(_failures[model]) from error
        try:
            _encoders[model] = TextCrossEncoder(model_name=model)
        except Exception as error:
            # A missing cache, an offline host or broken model files: recalling must not
            # fail because an optional quality stage cannot start, so degrade and report.
            _failures[model] = f"reranker {model} failed to load: {error}"
            raise RerankerUnavailable(_failures[model]) from error
        return _encoders[model]


def scores(model: str, query: str, documents: list[str]) -> list[float]:
    return [float(value) for value in load(model).rerank(query, documents)]
