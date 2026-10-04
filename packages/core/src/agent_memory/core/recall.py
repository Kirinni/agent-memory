"""Eligibility before relevance, then relevance × weight × recency. Zero LLM (ADR-002).

The default surface holds active files only. `--as-of` is the one reader of the history
surface, and it judges a file by its validity interval, never by its text alone.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import math
import pathlib
import sqlite3

from . import observation, reranker, timestamp
from .access_log import KIND_RECALL, AccessEntry, AccessLog
from .config import Config
from .database import SURFACE_ACTIVE, SURFACE_HISTORY, Database
from .record import MemoryRecord
from .search_index import LINK_SEPARATOR, Candidate, SearchIndex
from .store import Store
from .vector_index import VectorIndex

RRF_K = 60
RERANK_CAP_FRACTION = 0.999


def fuse_candidates(
    lexical: list[Candidate],
    dense: list[Candidate],
    pool: int,
    *,
    lexical_weight: float = 1.0,
    dense_weight: float = 1.0,
) -> list[Candidate]:
    """Reciprocal-rank fusion by chunk identity, normalized to a stable 0..1 scale.

    Weights let a store down-rank a leg that is noisy for its data (short Chinese
    queries on a mixed-language store, for instance) without switching it off.
    """

    def identity(item: Candidate) -> tuple[str, str, str, str]:
        return (item.name, item.kind, item.anchor, item.heading)

    scores: dict[tuple[str, str, str, str], float] = {}
    exemplars: dict[tuple[str, str, str, str], Candidate] = {}
    for candidates, weight in ((lexical, lexical_weight), (dense, dense_weight)):
        seen: set[tuple[str, str, str, str]] = set()
        for rank, candidate in enumerate(candidates, 1):
            key = identity(candidate)
            if key in seen:
                continue
            seen.add(key)
            exemplars.setdefault(key, candidate)
            scores[key] = scores.get(key, 0.0) + weight / (RRF_K + rank)
    maximum = (lexical_weight + dense_weight) / (RRF_K + 1)
    fused = [
        dataclasses.replace(exemplars[key], relevance=score / maximum)
        for key, score in scores.items()
    ]
    fused.sort(key=lambda item: (-item.relevance, identity(item)))
    return fused[:pool]


def _sigmoid(value: float) -> float:
    """Rerank logits live on an unbounded scale; relevance lives on 0..1."""
    if value >= 0:
        return 1.0 / (1.0 + math.exp(-value))
    factor = math.exp(value)
    return factor / (1.0 + factor)


@dataclasses.dataclass(frozen=True)
class Hit:
    name: str
    path: str
    abstract: str
    anchor: str
    heading: str
    type: str
    updated: str
    status: str
    weight: float
    relevance: float
    recency: float
    score: float
    source: str = "memory"
    provenance: tuple[str, ...] = ()
    cited_by: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, object]:
        payload = dataclasses.asdict(self)
        payload["provenance"] = list(self.provenance)
        payload["cited_by"] = list(self.cited_by)
        return payload


class Recall:
    def __init__(self, store: Store):
        self._store = store
        self._config: Config = store.config
        self._database = Database(store.layout)

    def recall(
        self,
        query: str,
        scope: str | None = None,
        as_of: str | None = None,
        limit: int | None = None,
        log: bool = True,
    ) -> list[Hit]:
        limit = self._config.recall.default_limit if limit is None else limit
        if limit < 1:
            raise ValueError("limit must be positive")
        if self._config.recall.max_limit > 0:
            limit = min(limit, self._config.recall.max_limit)
        pool = limit * self._config.recall.candidate_pool_multiplier
        scope_path = (
            "/".join(pathlib.PurePath(scope.strip("/\\")).parts) if scope else None
        )
        with self._database.connect() as connection:
            index = SearchIndex(connection)
            candidates = index.match(query, pool, SURFACE_ACTIVE, scope_path=scope_path)
            if as_of is not None:
                candidates = candidates + index.match(
                    query, pool, SURFACE_HISTORY, scope_path=scope_path
                )
            eligible = self._eligible(index.rows(), scope=scope, as_of=as_of)
            if self._config.index.vector_enabled:
                assert self._store.embedder is not None
                dense = VectorIndex(
                    connection, self._store.embedder, self._config.index.vector_model
                ).match(query, pool, eligible_names=set(eligible))
                if dense:
                    candidates = fuse_candidates(
                        candidates,
                        dense,
                        pool,
                        lexical_weight=self._config.recall.lexical_fusion_weight,
                        dense_weight=self._config.recall.dense_fusion_weight,
                    )
            if self._config.recall.rerank_enabled and len(candidates) > 1:
                candidates = self._rerank(query, candidates, eligible)
            hits = self._rank(candidates, eligible, as_of=as_of)
            hits = hits[:limit]
            if not log:
                return hits
            AccessLog(connection).append(
                [
                    AccessEntry(
                        self._store.clock.now().isoformat(),
                        hit.name,
                        query,
                        KIND_RECALL,
                        self._store.agent,
                    )
                    for hit in hits
                ]
            )
        observation.emit(
            "recall_return", query=query, scope=scope, as_of=as_of,
            effective_limit=limit, hits=[hit.as_dict() for hit in hits],
        )
        return hits

    def _rerank(
        self, query: str, candidates: list[Candidate], eligible: dict[str, sqlite3.Row]
    ) -> list[Candidate]:
        """The best candidates get a cross-encoder score; everything else is capped below them.

        Only the top `rerank_candidates` records are scored: scoring the long tail costs
        latency without changing the order a human would recognise.
        """
        settings = self._config.recall
        chosen: list[Candidate] = []
        seen: set[str] = set()
        ordered = sorted(
            candidates,
            key=lambda item: (-item.relevance, item.name, item.kind, item.anchor, item.heading),
        )
        for candidate in ordered:
            if candidate.name in seen or candidate.name not in eligible:
                continue
            seen.add(candidate.name)
            chosen.append(candidate)
            if len(chosen) >= max(1, settings.rerank_candidates):
                break
        try:
            scored = reranker.scores(
                settings.rerank_model,
                query,
                [self._rerank_text(candidate, eligible) for candidate in chosen],
            )
        except reranker.RerankerUnavailable as error:
            observation.emit("rerank_unavailable", model=settings.rerank_model, error=str(error))
            return candidates
        if len(scored) != len(chosen):
            observation.emit(
                "rerank_mismatch",
                model=settings.rerank_model,
                expected=len(chosen),
                got=len(scored),
            )
            return candidates
        boosted = {
            item.name: _sigmoid(score) for item, score in zip(chosen, scored, strict=True)
        }
        cap = min(boosted.values()) * RERANK_CAP_FRACTION
        return [
            dataclasses.replace(
                item,
                relevance=boosted[item.name] if item.name in boosted else min(item.relevance, cap),
            )
            for item in candidates
        ]

    def _rerank_text(self, candidate: Candidate, eligible: dict[str, sqlite3.Row]) -> str:
        row = eligible[candidate.name]
        body = ""
        try:
            text = (self._store.root / str(row["path"])).read_text(encoding="utf-8")
            body = MemoryRecord.from_text(text).body
        except Exception:
            # An unreadable or malformed file still has its indexed abstract to be scored on.
            body = ""
        return f"{str(row['abstract'])}\n{body[: self._config.recall.rerank_body_chars]}"

    def _eligible(
        self,
        rows: list[sqlite3.Row],
        scope: str | None,
        as_of: str | None,
    ) -> dict[str, sqlite3.Row]:
        eligible: dict[str, sqlite3.Row] = {}
        moment = timestamp.parse(as_of) if as_of else None
        for row in rows:
            name = str(row["name"])
            if scope and not self._in_scope(str(row["path"]), scope):
                continue
            if moment is None:
                if row["invalid_at"]:
                    continue
            elif not self._current_at(row, moment):
                continue
            eligible[name] = row
        return eligible

    def _in_scope(self, path: str, scope: str) -> bool:
        path_parts = pathlib.PurePath(path).parts
        scope_parts = pathlib.PurePath(scope.strip("/\\")).parts
        return bool(scope_parts) and path_parts[: len(scope_parts)] == scope_parts

    def _current_at(self, row: sqlite3.Row, moment: dt.datetime) -> bool:
        if timestamp.parse(str(row["valid_from"])) > moment:
            return False
        ended = row["invalid_at"]
        return not (ended and timestamp.parse(str(ended)) <= moment)

    def _rank(
        self,
        candidates: list[Candidate],
        eligible: dict[str, sqlite3.Row],
        as_of: str | None,
    ) -> list[Hit]:
        best: dict[str, tuple[float, str, str]] = {}
        for candidate in candidates:
            if candidate.name not in eligible:
                continue
            weighted = candidate.relevance * self._kind_weight(candidate.kind)
            current = best.get(candidate.name)
            if current is None or weighted > current[0]:
                best[candidate.name] = (weighted, candidate.anchor, candidate.heading)

        reference = timestamp.parse(as_of) if as_of else self._store.clock.now()
        hits: list[Hit] = []
        for name, (relevance, anchor, heading) in best.items():
            row = eligible[name]
            recency = self._recency(str(row["updated"]), reference)
            weight = float(row["weight"])
            hits.append(
                Hit(
                    name=name,
                    path=str(self._store.root / str(row["path"])),
                    abstract=str(row["abstract"]),
                    anchor=anchor,
                    heading=heading,
                    type=str(row["type"]),
                    updated=str(row["updated"]),
                    status="invalid" if row["invalid_at"] else "active",
                    weight=weight,
                    relevance=relevance,
                    recency=recency,
                    score=relevance * weight * recency,
                    provenance=tuple(
                        item for item in str(row["provenance"]).split(LINK_SEPARATOR) if item
                    ),
                )
            )
        hits.sort(key=lambda hit: (-hit.score, hit.name))
        return hits

    def _kind_weight(self, kind: str) -> float:
        from .chunking import KIND_ABSTRACT

        if kind == KIND_ABSTRACT:
            return self._config.index.bm25_abstract_weight
        return self._config.index.bm25_body_weight

    def _recency(self, updated: str, reference: dt.datetime) -> float:
        age_days = max(0.0, timestamp.days_between(reference, timestamp.parse(updated)))
        decayed = self._config.recall.recency_decay_base ** (
            age_days / self._config.recall.recency_half_life_days
        )
        return max(self._config.recall.recency_floor, decayed)
