"""The index tracks: a byte-prefix of MEMORY.md, never a summary of it.

`payload` is the deterministic floor of the three read tracks — it costs no tool call and
cannot miss. `slice_text` serves the same file to an explicit reader (the MCP `memory_index`
tool) under the same default budget."""

from __future__ import annotations

from .store import Store

NEWLINE = b"\n"
NEWLINE_TEXT = "\n"


def payload(store: Store) -> str:
    if not store.config.recall.injection_enabled:
        return ""
    return slice_text(store)[0]


def slice_text(store: Store, max_chars: int | None = None) -> tuple[str, bool]:
    """MEMORY.md at a chosen size: the configured byte prefix by default, or the first
    `max_chars` characters; either cut on a line boundary when one is available."""
    if not store.layout.memory_index.exists():
        return "", False
    data = store.layout.memory_index.read_bytes()
    if max_chars is None:
        budget = store.config.recall.injection_budget_bytes
        if len(data) <= budget:
            return data.decode("utf-8"), False
        cut = data.rfind(NEWLINE, 0, budget)
        return data[: cut if cut > 0 else budget].decode("utf-8", errors="ignore"), True
    text = data.decode("utf-8")
    if len(text) <= max_chars:
        return text, False
    cut = text.rfind(NEWLINE_TEXT, 0, max_chars)
    return text[: cut if cut > 0 else max_chars], True
