"""The MCP tools. Each one collapses onto the same core call the CLI makes."""

from __future__ import annotations

from agent_memory.core import injection
from agent_memory.core.errors import FieldError, ValidationError
from agent_memory.core.recall import Recall
from agent_memory.core.store import LEVEL_FULL, LEVELS, Store

TOOL_RECALL = "memory_recall"
TOOL_INDEX = "memory_index"
TOOL_READ = "memory_read"
TOOL_RECORD = "memory_record"
TOOL_CORRECT = "memory_correct"
TOOL_SUPERSEDE = "memory_supersede"
TOOL_DELETE = "memory_delete"
TOOL_TRACE = "memory_trace"
TOOL_MERGE = "memory_merge"
TOOL_FEEDBACK = "memory_feedback"


SCHEMAS: dict[str, dict[str, object]] = {
    TOOL_RECALL: {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Free text; Chinese and English both work (segmented + reranked).",
            },
            "scope": {
                "type": "string",
                "description": "Directory prefix to search within, e.g. reference/lvgl-simulator.",
            },
            "as_of": {
                "type": "string",
                "description": "ISO 8601 day or instant; returns memories valid at that time.",
            },
            "limit": {
                "type": "integer",
                "description": "Maximum hits (store caps it; default 8, max 50).",
            },
        },
        "required": ["query"],
    },
    TOOL_INDEX: {
        "type": "object",
        "properties": {
            "max_chars": {
                "type": "integer",
                "description": (
                    "Characters to return; default is the configured prefix (~8 KB). "
                    "Pass a large value for the whole index."
                ),
            },
            "offset": {
                "type": "integer",
                "description": (
                    "Character offset to continue from; use the previous response's next_offset."
                ),
            },
        },
    },
    TOOL_READ: {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "The memory's slug, as listed in the index."},
            "level": {
                "type": "string",
                "enum": list(LEVELS),
                "description": "outline = headings and lead lines; full = the whole body.",
            },
            "max_chars": {
                "type": "integer",
                "description": (
                    "Cap the returned text; 0 reads everything; default follows the store's "
                    "read budget."
                ),
            },
        },
        "required": ["name"],
    },
    TOOL_RECORD: {
        "type": "object",
        "properties": {
            "abstract": {
                "type": "string",
                "description": (
                    "One self-contained line (<=240 chars): this is what the index and search "
                    "show."
                ),
            },
            "type": {
                "type": "string",
                "description": (
                    "An existing type, e.g. decision, experience, reference, preference, fact."
                ),
            },
            "fields": {
                "type": "object",
                "additionalProperties": {"type": "string"},
                "description": (
                    "The type's schema fields (e.g. project, topic); they decide placement."
                ),
            },
            "body": {
                "type": "string",
                "description": "Markdown body: the durable detail behind the abstract.",
            },
            "name": {
                "type": "string",
                "description": "Kebab-case slug; omit to derive one from the abstract.",
            },
            "create_group": {
                "type": "boolean",
                "description": "Create the group directory when it does not exist yet.",
            },
            "pinned": {
                "type": "boolean",
                "description": (
                    "Keep it in the index's Pinned section, exempt from the budget "
                    "(for must-know rules)."
                ),
            },
            "links": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Names of related active memories.",
            },
            "provenance": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Citations: message excerpts or stored pointers.",
            },
            "supersedes": {
                "type": "string",
                "description": "Name of an active memory this one replaces; its validity ends.",
            },
        },
        "required": ["abstract", "type"],
    },
    TOOL_CORRECT: {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "The memory to update."},
            "abstract": {
                "type": "string",
                "description": "Replacement abstract (one line, <=240 chars).",
            },
            "body": {"type": "string", "description": "Replacement body."},
            "supersede_with": {
                "type": "string",
                "description": "Name of an existing active memory that replaces this one.",
            },
            "pinned": {"type": "boolean", "description": "Pin or unpin it in the root index."},
            "links": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Replace all links; empty list removes all links",
            },
            "provenance": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Additional citations to append.",
            },
        },
        "required": ["name"],
    },
    TOOL_SUPERSEDE: {
        "type": "object",
        "properties": {
            "old": {"type": "string", "description": "The memory whose validity ends."},
            "new": {
                "type": "string",
                "description": "The existing active memory that replaces it.",
            },
        },
        "required": ["old", "new"],
    },
    TOOL_DELETE: {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "The memory to end; its file and history are retained.",
            }
        },
        "required": ["name"],
    },
    TOOL_TRACE: {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "The memory whose cited messages to read."},
            "pointer": {
                "type": "string",
                "description": "Read one stored pointer instead of every citation.",
            },
        },
        "required": ["name"],
    },
    TOOL_MERGE: {
        "type": "object",
        "properties": {
            "names": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Two or more active memories to combine.",
            },
            "name": {
                "type": "string",
                "description": "Slug for the merged memory; omit to derive.",
            },
            "abstract": {
                "type": "string",
                "description": "Abstract of the merged memory (one line).",
            },
            "body": {"type": "string", "description": "Body of the merged memory."},
        },
        "required": ["names", "abstract", "body"],
    },
    TOOL_FEEDBACK: {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "The memory to weigh."},
            "direction": {
                "type": "string",
                "enum": ["boost", "penalize"],
                "description": "boost when it proved useful; penalize when it misled.",
            },
        },
        "required": ["name", "direction"],
    },
}

DESCRIPTIONS = {
    TOOL_RECALL: (
        "Search the store and return candidate summaries. Use for a specific question; call "
        "memory_index first when you do not know what the store holds."
    ),
    TOOL_INDEX: (
        "Read the root index: pinned first, then by weight, newest first. Default returns the "
        "configured prefix (~8 KB) and reports listed/total; max_chars reads further, and "
        "offset (with the previous next_offset) pages through the rest."
    ),
    TOOL_READ: "Read one memory at a chosen level of detail (outline or full).",
    TOOL_RECORD: (
        "Write one memory. The abstract is what the index and search show: keep it one "
        "self-contained line."
    ),
    TOOL_CORRECT: "Update a memory in place, pin or unpin it, or supersede it with a newer one.",
    TOOL_SUPERSEDE: "End an old memory's validity in favor of an existing active memory.",
    TOOL_DELETE: "End a named memory's validity while retaining historical evidence.",
    TOOL_TRACE: "Read only the archived messages cited by a named memory.",
    TOOL_MERGE: "Combine named active memories atomically and retain their history.",
    TOOL_FEEDBACK: "Raise or lower a memory's weight explicitly.",
}


def catalogue() -> list[dict[str, object]]:
    return [
        {"name": name, "description": DESCRIPTIONS[name], "inputSchema": SCHEMAS[name]}
        for name in SCHEMAS
    ]


def dispatch(store: Store, tool: str, arguments: dict[str, object]) -> dict[str, object]:
    if tool not in SCHEMAS:
        raise ValidationError([FieldError("tool", f"unknown tool: {tool}")])
    _require(tool, arguments)
    handler = _HANDLERS[tool]
    return handler(store, arguments)


def _require(tool: str, arguments: dict[str, object]) -> None:
    if "links" in arguments and (
        not isinstance(arguments["links"], list)
        or not all(isinstance(item, str) for item in arguments["links"])
    ):
        raise ValidationError([FieldError("links", "must be an array of memory names")])
    if "names" in arguments and (
        not isinstance(arguments["names"], list)
        or not all(isinstance(item, str) for item in arguments["names"])
    ):
        raise ValidationError([FieldError("names", "must be an array of memory names")])
    if "provenance" in arguments and (
        not isinstance(arguments["provenance"], list)
        or not all(isinstance(item, str) for item in arguments["provenance"])
    ):
        raise ValidationError([FieldError("provenance", "must be an array of references")])
    if "pinned" in arguments and not isinstance(arguments["pinned"], bool):
        raise ValidationError([FieldError("pinned", "must be a boolean")])
    schema = SCHEMAS[tool]
    required = schema.get("required")
    missing = [
        field
        for field in (required if isinstance(required, list) else [])
        if not str(arguments.get(field, "")).strip()
    ]
    if missing:
        raise ValidationError([FieldError(field, "required") for field in missing])
    properties = schema.get("properties")
    unknown = set(arguments) - set(properties if isinstance(properties, dict) else {})
    if unknown:
        names = ", ".join(sorted(unknown))
        raise ValidationError([FieldError("arguments", f"unknown field: {names}")])
    for field, rules in (properties if isinstance(properties, dict) else {}).items():
        allowed = rules.get("enum") if isinstance(rules, dict) else None
        value = arguments.get(field)
        if allowed and value is not None and value not in allowed:
            raise ValidationError([FieldError(field, f"must be one of {', '.join(allowed)}")])


def _recall(store: Store, arguments: dict[str, object]) -> dict[str, object]:
    hits = Recall(store).recall(
        str(arguments["query"]),
        scope=_optional(arguments, "scope"),
        as_of=_optional(arguments, "as_of"),
        limit=int(str(arguments["limit"])) if arguments.get("limit") else None,
    )
    return {
        "query": str(arguments["query"]),
        "recall_fingerprint": store.config.recall_fingerprint(),
        "hits": [hit.as_dict() for hit in hits],
    }


def _read(store: Store, arguments: dict[str, object]) -> dict[str, object]:
    result = store.read(
        str(arguments["name"]),
        level=str(arguments.get("level") or LEVEL_FULL),
        max_chars=_int_argument(arguments, "max_chars"),
    )
    return {
        "name": result.record.name,
        "level": result.level,
        "abstract": result.record.abstract,
        "path": str(result.record.path),
        "outline": list(result.outline),
        "text": result.text,
        "truncated": result.truncated,
        "pinned": result.record.pinned,
        "provenance": list(result.record.provenance),
    }


def _index(store: Store, arguments: dict[str, object]) -> dict[str, object]:
    max_chars = _int_argument(arguments, "max_chars")
    if max_chars is not None and max_chars <= 0:
        raise ValidationError([FieldError("max_chars", "must be a positive integer")])
    offset = _int_argument(arguments, "offset") or 0
    if offset < 0:
        raise ValidationError([FieldError("offset", "must be zero or a positive integer")])
    text, truncated, next_offset = injection.slice_text(store, max_chars, offset)
    payload: dict[str, object] = {
        "path": str(store.layout.memory_index),
        "text": text,
        "truncated": truncated,
        "listed": sum(1 for line in text.splitlines() if line.startswith("- [")),
        "total": len(store.records()),
    }
    if truncated:
        payload["next_offset"] = next_offset
    return payload


def _record(store: Store, arguments: dict[str, object]) -> dict[str, object]:
    written = store.record(
        abstract=str(arguments["abstract"]),
        type=str(arguments["type"]),
        fields=_string_map(arguments.get("fields")),
        body=str(arguments.get("body") or ""),
        name=_optional(arguments, "name"),
        create_group=bool(arguments.get("create_group")),
        pinned=_bool_argument(arguments, "pinned"),
        links=_string_list(arguments.get("links")),
        provenance=_string_list(arguments.get("provenance")),
        supersedes=_optional(arguments, "supersedes"),
    )
    return {"name": written.name, "path": str(written.path), "updated": written.updated}


def _correct(store: Store, arguments: dict[str, object]) -> dict[str, object]:
    corrected = store.correct(
        str(arguments["name"]),
        abstract=_optional(arguments, "abstract"),
        body=_optional(arguments, "body"),
        supersede_with=_optional(arguments, "supersede_with"),
        links=_string_list(arguments["links"]) if "links" in arguments else None,
        provenance=_string_list(arguments["provenance"]) if "provenance" in arguments else None,
        pinned=_bool_argument(arguments, "pinned"),
    )
    return {
        "name": corrected.name,
        "superseded_by": corrected.superseded_by,
        "updated": corrected.updated,
    }


def _feedback(store: Store, arguments: dict[str, object]) -> dict[str, object]:
    step = store.config.weight.boost_step
    delta = step if arguments["direction"] == "boost" else -step
    updated = store.feedback(str(arguments["name"]), delta)
    return {"name": updated.name, "weight": updated.weight}


def _supersede(store: Store, arguments: dict[str, object]) -> dict[str, object]:
    replaced = store.supersede(str(arguments["old"]), str(arguments["new"]))
    return {"name": replaced.name, "superseded_by": replaced.superseded_by,
            "invalid_at": replaced.invalid_at}


def _delete(store: Store, arguments: dict[str, object]) -> dict[str, object]:
    removed = store.delete(str(arguments["name"]))
    return {"name": removed.name, "status": removed.status, "invalid_at": removed.invalid_at}


def _trace(store: Store, arguments: dict[str, object]) -> dict[str, object]:
    return store.trace_evidence(str(arguments["name"]), _optional(arguments, "pointer")).as_dict()


def _merge(store: Store, arguments: dict[str, object]) -> dict[str, object]:
    merged = store.merge(
        _string_list(arguments["names"]), str(arguments["abstract"]),
        str(arguments["body"]), name=_optional(arguments, "name"),
    )
    return {"name": merged.name, "path": str(merged.path),
            "sources": _string_list(arguments["names"])}


def _optional(arguments: dict[str, object], key: str) -> str | None:
    value = arguments.get(key)
    return str(value) if value is not None and str(value) != "" else None


def _int_argument(arguments: dict[str, object], key: str) -> int | None:
    value = arguments.get(key)
    if value is None:
        return None
    try:
        return int(str(value))
    except ValueError as error:
        raise ValidationError([FieldError(key, "must be an integer")]) from error


def _bool_argument(arguments: dict[str, object], key: str) -> bool | None:
    value = arguments.get(key)
    return value if isinstance(value, bool) else None


def _string_map(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {str(key): str(item) for key, item in value.items()}


def _string_list(value: object) -> list[str]:
    return [str(item) for item in value] if isinstance(value, list) else []


_HANDLERS = {
    TOOL_RECALL: _recall,
    TOOL_INDEX: _index,
    TOOL_READ: _read,
    TOOL_RECORD: _record,
    TOOL_CORRECT: _correct,
    TOOL_SUPERSEDE: _supersede,
    TOOL_DELETE: _delete,
    TOOL_TRACE: _trace,
    TOOL_MERGE: _merge,
    TOOL_FEEDBACK: _feedback,
}
