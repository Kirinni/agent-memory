"""Read budgets, recall clamps, and the MCP wire shape."""

import json

import pytest
from agent_memory.core.errors import ValidationError
from agent_memory.core.recall import Recall
from agent_memory.core.store import LEVEL_OUTLINE
from agent_memory.mcp import server, tools


def _call(store, tool, arguments):
    return server.handle(
        store,
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": tool, "arguments": arguments},
        },
    )


def test_recall_limit_is_clamped_to_the_configured_maximum(store):
    store.config.recall.max_limit = 2
    for index in range(5):
        store.record(abstract=f"budget probe {index}", type="decision", name=f"budget-{index}")
    hits = Recall(store).recall("budget probe", limit=50)
    assert len(hits) == 2


def test_read_budget_truncates_and_reports(store):
    store.record(abstract="long note", type="reference", body="x" * 300, name="long-note")
    result = store.read("long-note", max_chars=100)
    assert result.truncated
    assert len(result.text) < 300
    assert "max_chars=0" in result.text
    full = store.read("long-note", max_chars=0)
    assert full.text == "x" * 300
    assert not full.truncated


def test_read_follows_the_configured_budget(store):
    store.record(
        abstract="budget default", type="reference", body="y" * 300, name="budget-default"
    )
    store.config.recall.read_max_chars = 120
    assert store.read("budget-default").truncated


def test_a_negative_budget_is_rejected(store):
    store.record(abstract="negative", type="reference", body="z", name="negative-budget")
    with pytest.raises(ValidationError):
        store.read("negative-budget", max_chars=-1)


def test_mcp_call_can_omit_structured_content(store):
    store.config.mcp.emit_structured_content = False
    response = _call(store, tools.TOOL_RECALL, {"query": "anything"})
    result = response["result"]
    assert "structuredContent" not in result
    assert json.loads(result["content"][0]["text"])["hits"] == []


def test_mcp_call_returns_structured_content_by_default(store):
    response = _call(store, tools.TOOL_RECALL, {"query": "anything"})
    assert "structuredContent" in response["result"]


def test_mcp_read_reports_truncation(store):
    store.record(abstract="mcp budget", type="reference", body="z" * 300, name="mcp-budget")
    response = _call(store, tools.TOOL_READ, {"name": "mcp-budget", "max_chars": 80})
    payload = json.loads(response["result"]["content"][0]["text"])
    assert payload["truncated"] is True
    assert len(payload["text"]) < 300


def test_mcp_read_rejects_a_non_integer_budget(store):
    store.record(abstract="mcp budget bad", type="reference", body="z", name="mcp-budget-bad")
    response = _call(
        store, tools.TOOL_READ, {"name": "mcp-budget-bad", "max_chars": "soon"}
    )
    assert "error" in response


def test_headingless_notes_get_a_fallback_outline(store):
    body = (
        "- 触发: 扫码枪(串口) + DUT 在位检测 双条件；\n"
        "- NG 流程: 不合格锁线。\n"
        "- 无声音能力: 屏幕提示必须自足。"
    )
    store.record(abstract="outline probe", type="decision", body=body, name="outline-probe")
    result = store.read("outline-probe", level=LEVEL_OUTLINE)
    assert len(result.outline) == 3
    assert result.outline[0].startswith("触发")
