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


def _index_payload(store, arguments=None):
    response = _call(store, tools.TOOL_INDEX, arguments or {})
    return json.loads(response["result"]["content"][0]["text"])


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


def test_mcp_index_serves_the_root_index(store):
    store.record(abstract="indexed note", type="decision", name="indexed-note")
    payload = _index_payload(store)
    assert payload["truncated"] is False
    assert "- [indexed-note](" in payload["text"]
    assert payload["path"].endswith("MEMORY.md")


def test_mcp_index_follows_the_injection_budget(store):
    for index in range(30):
        store.record(
            abstract=f"index filler {index}", type="reference", name=f"index-filler-{index}"
        )
    store.config.recall.injection_budget_bytes = 300
    payload = _index_payload(store)
    assert payload["truncated"] is True
    assert len(payload["text"].encode("utf-8")) <= 300
    assert store.layout.memory_index.read_text(encoding="utf-8").startswith(payload["text"])


def test_mcp_index_max_chars_cuts_on_a_line_boundary(store):
    for index in range(30):
        store.record(
            abstract=f"index slice {index}", type="reference", name=f"index-slice-{index}"
        )
    full = store.layout.memory_index.read_text(encoding="utf-8")
    payload = _index_payload(store, {"max_chars": 120})
    assert payload["truncated"] is True
    assert len(payload["text"]) <= 120
    assert full.startswith(payload["text"])
    # The page keeps its trailing newline so the next page starts exactly at its end.
    assert payload["text"].endswith("\n")


def test_mcp_index_ignores_the_injection_switch(store):
    store.config.recall.injection_enabled = False
    store.record(abstract="still listed", type="decision", name="still-listed")
    payload = _index_payload(store)
    assert payload["truncated"] is False
    assert "still-listed" in payload["text"]


def test_mcp_index_rejects_a_non_positive_budget(store):
    assert "error" in _call(store, tools.TOOL_INDEX, {"max_chars": 0})


def test_mcp_index_reports_listed_and_total(store):
    for index in range(5):
        store.record(abstract=f"index row {index}", type="fact", name=f"index-row-{index}")
    payload = _index_payload(store)
    assert payload["total"] == 5
    assert payload["listed"] == 5
    assert "next_offset" not in payload


def test_mcp_index_reports_a_shortfall_when_the_budget_cuts(store):
    for index in range(30):
        store.record(abstract=f"filler {index}", type="fact", name=f"filler-{index}")
    store.config.recall.injection_budget_bytes = 300
    payload = _index_payload(store)
    assert payload["truncated"] is True
    assert payload["listed"] < payload["total"] == 30
    assert payload["next_offset"] > 0


def test_mcp_index_pages_with_offset(store):
    for index in range(40):
        store.record(
            abstract=f"page probe {index} with enough text to matter", type="fact",
            name=f"page-{index}",
        )
    full = _index_payload(store, {"max_chars": 100000})["text"]
    first = _index_payload(store, {"max_chars": 400})
    assert first["truncated"] is True
    second = _index_payload(store, {"max_chars": 400, "offset": first["next_offset"]})
    assert full.startswith(first["text"] + second["text"])


def test_mcp_index_rejects_a_negative_offset(store):
    assert "error" in _call(store, tools.TOOL_INDEX, {"offset": -1})


def test_every_tool_parameter_is_documented():
    for name, schema in tools.SCHEMAS.items():
        properties = schema["properties"]
        assert properties, f"{name} declares no properties"
        for parameter, rules in properties.items():
            assert rules.get("description"), f"{name}.{parameter} has no description"


def test_tool_metadata_stays_ascii():
    # mcp-proxy relays tools/list through a text layer that replaces non-ASCII bytes, so
    # anything beyond ASCII reaches clients as U+FFFD. Keep the metadata ASCII.
    for name, schema in tools.SCHEMAS.items():
        assert tools.DESCRIPTIONS[name].isascii(), f"{name} description is not ASCII"
        for parameter, rules in schema["properties"].items():
            assert rules["description"].isascii(), f"{name}.{parameter} description is not ASCII"
