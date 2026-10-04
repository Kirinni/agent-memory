"""Offline retrieval evaluation over a copied store.

Runs recall queries against a throwaway copy of a store, so the store is never mutated.
Works from either checkout: the importing interpreter decides which agent_memory
implementation is measured.

The query file is JSON, either a bare list or ``{"queries": [...]}``, where each entry
names the memory files that should answer it:

    {"queries": [
        {"query": "空调网关地址", "expected": ["device-aircon"], "lang": "zh"},
        {"query": "aircon gateway address", "expected": ["device-aircon"], "lang": "en"}
    ]}

Usage:
    python tools/retrieval_eval.py --store /path/to/store --queries /path/to/queries.json
    python tools/retrieval_eval.py --store ... --queries ... --vector --vector-model <model>
"""

from __future__ import annotations

import argparse
import json
import pathlib
import shutil
import tempfile
import time


def _load_queries(path: pathlib.Path) -> list[dict[str, object]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        payload = payload["queries"]
    return list(payload)


def _stats(rows: list[dict[str, object]]) -> dict[str, object]:
    count = len(rows)
    top1 = sum(row["top1"] for row in rows) / count
    top3 = sum(row["top3"] for row in rows) / count
    mrr = sum(1.0 / row["rank"] for row in rows if row["rank"]) / count
    return {"queries": count, "top1": round(top1, 3), "top3": round(top3, 3), "mrr": round(mrr, 3)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", required=True)
    parser.add_argument("--queries", required=True)
    parser.add_argument("--vector", action="store_true", help="enable the vector leg")
    parser.add_argument("--vector-model", default=None)
    parser.add_argument("--lexical-weight", type=float, default=None)
    parser.add_argument("--dense-weight", type=float, default=None)
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--label", default="run")
    parser.add_argument("--mcp-payload", action="store_true", help="report raw MCP payload sizes")
    parser.add_argument("--json-out", default=None)
    arguments = parser.parse_args(argv)

    from agent_memory.core.config import Config
    from agent_memory.core.recall import Recall
    from agent_memory.core.store import Store

    workdir = pathlib.Path(tempfile.mkdtemp(prefix="retrieval-eval-"))
    try:
        root = workdir / "store"
        shutil.copytree(arguments.store, root)
        for junk in (".index", ".state"):
            shutil.rmtree(root / junk, ignore_errors=True)
        config = Config.load(root)
        config.index.vector_enabled = bool(arguments.vector)
        if arguments.vector_model:
            config.index.vector_model = arguments.vector_model
        if arguments.lexical_weight is not None and hasattr(config.recall, "lexical_fusion_weight"):
            config.recall.lexical_fusion_weight = arguments.lexical_weight
        if arguments.dense_weight is not None and hasattr(config.recall, "dense_fusion_weight"):
            config.recall.dense_fusion_weight = arguments.dense_weight
        config.save(root)

        started = time.monotonic()
        store = Store(root)
        report = store.rebuild_index()
        build_seconds = time.monotonic() - started

        rows: list[dict[str, object]] = []
        for item in _load_queries(pathlib.Path(arguments.queries)):
            query = str(item["query"])
            expected = [str(name) for name in item.get("expected", [])]
            started = time.monotonic()
            hits = Recall(store).recall(query, limit=arguments.limit, log=False)
            elapsed = time.monotonic() - started
            names = [hit.name for hit in hits]
            rank = next((index for index, name in enumerate(names, 1) if name in expected), None)
            rows.append(
                {
                    "query": query,
                    "lang": str(item.get("lang", "")),
                    "expected": expected,
                    "hits": names,
                    "rank": rank,
                    "top1": rank == 1,
                    "top3": rank is not None and rank <= 3,
                    "seconds": round(elapsed, 3),
                }
            )

        summary: dict[str, object] = {
            "label": arguments.label,
            "vector": bool(arguments.vector),
            "vector_model": config.index.vector_model if arguments.vector else None,
            "reindexed": len(report.reindexed),
            "build_seconds": round(build_seconds, 2),
            "overall": _stats(rows),
        }
        if hasattr(config.recall, "lexical_fusion_weight"):
            summary["fusion_weights"] = {
                "lexical": config.recall.lexical_fusion_weight,
                "dense": config.recall.dense_fusion_weight,
            }
        by_lang = {}
        for lang in sorted({row["lang"] for row in rows}):
            by_lang[lang] = _stats([row for row in rows if row["lang"] == lang])
        summary["by_lang"] = by_lang

        if arguments.mcp_payload and hasattr(config, "mcp"):
            from agent_memory.mcp import server as mcp_server

            sizes = {}
            for flag in (True, False):
                store.config.mcp.emit_structured_content = flag
                response = mcp_server.handle(
                    store,
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "tools/call",
                        "params": {
                            "name": "memory_recall",
                            "arguments": {"query": rows[0]["query"]},
                        },
                    },
                )
                sizes[str(flag)] = len(json.dumps(response, ensure_ascii=False).encode("utf-8"))
            summary["mcp_payload_bytes"] = sizes

        print(json.dumps(summary, ensure_ascii=False))
        for row in rows:
            flag = "ok  " if row["top1"] else ("top3" if row["top3"] else "MISS")
            print(f"[{flag}] {row['query']} -> {row['hits'][:3]}")
        if arguments.json_out:
            pathlib.Path(arguments.json_out).write_text(
                json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
