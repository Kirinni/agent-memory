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


def slice_text(
    store: Store, max_chars: int | None = None, offset: int = 0
) -> tuple[str, bool, int]:
    """The root index at a chosen position and size.

    Default (no budget, no offset) is the injection track's configured byte prefix — the
    same bytes a hooked host receives. An explicit budget or offset switches to character
    paging: the page starts at the next line boundary at or after `offset` and ends at the
    last line boundary within the budget (unless one line alone exceeds it). Returns
    `(text, truncated, next_offset)`; `next_offset` is where the next page starts, 0 when
    the page is complete.
    """
    if not store.layout.memory_index.exists():
        return "", False, 0
    data = store.layout.memory_index.read_bytes()
    if max_chars is None and offset == 0:
        budget = store.config.recall.injection_budget_bytes
        if len(data) <= budget:
            return data.decode("utf-8"), False, 0
        cut = data.rfind(NEWLINE, 0, budget)
        keep = cut if cut > 0 else budget
        text = data[:keep].decode("utf-8", errors="ignore")
        return text, True, len(text) + 1 if cut > 0 else len(text)
    text = data.decode("utf-8")
    start = max(offset, 0)
    if start > 0 and text[start - 1] != NEWLINE_TEXT:
        boundary = text.find(NEWLINE_TEXT, start)
        start = boundary + 1 if boundary != -1 else len(text)
    page_chars = max_chars if max_chars is not None else store.config.recall.injection_budget_bytes
    chunk = text[start : start + page_chars]
    end = start + len(chunk)
    if end < len(text):
        cut = chunk.rfind(NEWLINE_TEXT)
        if cut > 0:
            chunk = chunk[: cut + 1]
            end = start + len(chunk)
    truncated = end < len(text)
    return chunk, truncated, end if truncated else 0
