"""Pinned memories: marked in the file, led in the root index, exempt from its budget."""

import json

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


def _index_text(store) -> str:
    return store.layout.memory_index.read_text(encoding="utf-8")


def test_a_pinned_record_leads_the_index(store):
    store.record(abstract="ordinary note", type="fact", name="ordinary-note")
    store.record(abstract="must-know rule", type="fact", name="must-know-rule", pinned=True)
    text = _index_text(store)
    assert "## Pinned" in text
    pinned_at = text.index("must-know-rule")
    ordinary_at = text.index("ordinary-note")
    assert pinned_at < ordinary_at


def test_indexes_without_pins_are_byte_identical_to_before(seeded):
    text = _index_text(seeded)
    assert "## Pinned" not in text


def test_pinned_survives_the_weight_floor(store):
    store.record(
        abstract="faded but pinned", type="fact", name="faded-rule", weight=0.1, pinned=True
    )
    text = _index_text(store)
    assert "faded-rule" in text


def test_pinned_lines_are_not_cut_by_the_budget(store):
    store.config.memory_md.budget_bytes = len(store.config.memory_md.header) + len("\n\n") + 10
    store.record(abstract="kept", type="fact", name="kept-rule", pinned=True)
    for index in range(20):
        store.record(abstract=f"filler {index}", type="fact", name=f"filler-{index}")
    text = _index_text(store)
    assert "kept-rule" in text
    assert "filler-" not in text


def test_pin_is_persisted_in_the_frontmatter(store):
    written = store.record(abstract="durable pin", type="fact", name="durable-pin", pinned=True)
    assert "pinned: true" in written.path.read_text(encoding="utf-8")
    reloaded = store.find("durable-pin")
    assert reloaded is not None and reloaded.pinned is True


def test_an_update_without_the_flag_preserves_the_pin(store):
    store.record(abstract="pin stays", type="fact", name="pin-stays", pinned=True)
    store.record(abstract="pin stays, reworded", type="fact", name="pin-stays")
    assert store.find("pin-stays").pinned is True


def test_correct_pins_and_unpins(store):
    store.record(abstract="toggled", type="fact", name="toggled")
    assert store.find("toggled").pinned is False
    assert store.correct("toggled", pinned=True).pinned is True
    assert store.correct("toggled", pinned=False).pinned is False


def test_merge_inherits_a_source_pin(store):
    store.record(abstract="first half", type="fact", name="merge-first")
    store.record(abstract="second half", type="fact", name="merge-second", pinned=True)
    merged = store.merge(["merge-first", "merge-second"], "both halves", "combined body")
    assert merged.pinned is True


def test_mcp_record_and_read_carry_the_pin(store):
    _call(store, tools.TOOL_RECORD, {"abstract": "via mcp", "type": "fact",
                                     "name": "mcp-pin", "pinned": True})
    payload = json.loads(
        _call(store, tools.TOOL_READ, {"name": "mcp-pin"})["result"]["content"][0]["text"]
    )
    assert payload["pinned"] is True


def test_mcp_correct_toggles_the_pin(store):
    store.record(abstract="mcp toggle", type="fact", name="mcp-toggle")
    _call(store, tools.TOOL_CORRECT, {"name": "mcp-toggle", "pinned": True})
    assert store.find("mcp-toggle").pinned is True
    _call(store, tools.TOOL_CORRECT, {"name": "mcp-toggle", "pinned": False})
    assert store.find("mcp-toggle").pinned is False


def test_mcp_rejects_a_non_boolean_pin(store):
    response = _call(
        store,
        tools.TOOL_RECORD,
        {"abstract": "bad pin", "type": "fact", "name": "bad-pin", "pinned": "yes"},
    )
    assert "error" in response


def test_injection_still_leads_with_the_pinned_section(store):
    from agent_memory.core import injection

    store.record(abstract="leads injection", type="fact", name="leads-injection", pinned=True)
    store.record(abstract="follows", type="fact", name="follows-injection")
    payload = injection.payload(store)
    assert payload.index("leads-injection") < payload.index("follows-injection")
