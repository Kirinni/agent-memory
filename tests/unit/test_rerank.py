"""M10 — the rerank stage is optional quality: it reorders, never raises, never hides."""

import sys
import types

import pytest
from agent_memory.core import reranker
from agent_memory.core.recall import Recall
from agent_memory.core.reranker import RerankerUnavailable

MODEL = "test/rerank-model"


class FakeEncoder:
    def __init__(self, scores):
        self.scores = scores
        self.calls = []

    def rerank(self, query, documents):
        self.calls.append(list(documents))
        return list(self.scores[: len(documents)])


def _names(hits):
    return [hit.name for hit in hits]


def _seed_ranked(store):
    store.record(
        abstract="deploy rollout deploy rollout deploy",
        type="reference",
        name="gate-a",
        body="deploy rollout details a",
    )
    store.record(
        abstract="deploy rollout gate b",
        type="reference",
        name="gate-b",
        body="deploy rollout details b",
    )
    store.record(
        abstract="deploy rollout gate c",
        type="reference",
        name="gate-c",
        body="deploy rollout details c",
    )
    store.record(
        abstract="deploy rollout gate d",
        type="reference",
        name="gate-d",
        body="deploy rollout details d",
    )


def test_the_best_scored_candidate_comes_first_and_the_rest_are_capped(store, monkeypatch):
    _seed_ranked(store)
    lexical = _names(Recall(store).recall("deploy rollout gate", log=False))
    assert len(lexical) == 4

    store.config.recall.rerank_enabled = True
    store.config.recall.rerank_candidates = 3
    fake = FakeEncoder([-6.0, 0.0, 6.0])
    monkeypatch.setattr(reranker, "load", lambda model: fake)

    hits = Recall(store).recall("deploy rollout gate", log=False)
    assert _names(hits)[:3] == [lexical[2], lexical[1], lexical[0]]
    assert _names(hits)[3] == lexical[3]
    assert hits[3].relevance < hits[2].relevance
    documents = fake.calls[0]
    assert len(documents) == 3
    assert "deploy rollout details a" in documents[0]
    assert len({document.split()[-1] for document in documents}) == 3


def test_a_reranker_that_cannot_load_keeps_the_fused_order(store, monkeypatch):
    _seed_ranked(store)
    lexical = _names(Recall(store).recall("deploy rollout gate", log=False))

    store.config.recall.rerank_enabled = True

    def unavailable(model):
        raise RerankerUnavailable("model cache missing")

    monkeypatch.setattr(reranker, "load", unavailable)
    assert _names(Recall(store).recall("deploy rollout gate", log=False)) == lexical


def test_a_load_failure_is_remembered_not_retried(monkeypatch):
    monkeypatch.setitem(sys.modules, "fastembed.rerank.cross_encoder", None)
    with pytest.raises(RerankerUnavailable, match="optional dependency"):
        reranker.load("cache-miss-model")

    module = types.ModuleType("fastembed.rerank.cross_encoder")

    class Boom:
        def __init__(self, model_name):
            raise RuntimeError("no")

    module.TextCrossEncoder = Boom
    monkeypatch.setitem(sys.modules, "fastembed.rerank.cross_encoder", module)
    # The remembered failure wins: a retry would report "failed to load" instead.
    with pytest.raises(RerankerUnavailable, match="optional dependency"):
        reranker.load("cache-miss-model")
