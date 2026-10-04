"""The always-injected root index. A hard budget for the ranked body; pinned lines are
exempt — a pinned memory is one the store is told never to drop from the index."""

from __future__ import annotations

from .config import Config
from .paths import StoreLayout
from .record import MemoryRecord


def render(records: list[MemoryRecord], config: Config, root: str = "") -> str:
    pinned = [record for record in records if record.is_active() and record.pinned]
    eligible = [
        record
        for record in records
        if record.is_active()
        and not record.pinned
        and record.weight >= config.recall.memory_md_weight_floor
    ]
    _sort_by_weight(pinned)
    _sort_by_weight(eligible)

    header = config.memory_md.header + "\n\n"
    parts: list[str] = [header]
    used = len(header.encode("utf-8"))
    if pinned:
        section = config.memory_md.pinned_header + "\n\n"
        parts.append(section)
        used += len(section.encode("utf-8"))
        for record in pinned:
            line = _line(record, root)
            parts.append(line)
            used += len(line.encode("utf-8"))
        parts.append("\n")
        used += 1

    for index, record in enumerate(eligible):
        if index >= config.memory_md.max_lines:
            break
        line = _line(record, root)
        cost = len(line.encode("utf-8"))
        if used + cost > config.memory_md.budget_bytes:
            break
        parts.append(line)
        used += cost
    return "".join(parts)


def write(layout: StoreLayout, records: list[MemoryRecord]) -> str:
    text = render(records, layout.config, str(layout.root))
    layout.memory_index.write_text(text, encoding="utf-8")
    return text


def _sort_by_weight(records: list[MemoryRecord]) -> None:
    """Weight first, then freshest. Equal-weight records lead with the most recently
    updated so that an index over budget sheds its stalest lines, not its newest —
    new and freshly corrected memories stay visible until they earn or lose weight."""
    records.sort(key=lambda record: record.name)
    records.sort(key=lambda record: record.updated, reverse=True)
    records.sort(key=lambda record: record.weight, reverse=True)


def _line(record: MemoryRecord, root: str) -> str:
    location = str(record.path)
    if root and location.startswith(root):
        location = location[len(root) :].lstrip("/")
    return f"- [{record.name}]({location}) — {record.abstract}\n"
