"""Self-install: probe the host, write its hook dialect and skill, leave everything else alone."""

from __future__ import annotations

import json
import os
import pathlib
import shlex
import shutil
import subprocess
import sys
import tempfile

from agent_memory.core import prompts
from agent_memory.core.errors import FieldError, ValidationError

from . import moments

HOOK_COMMAND = "mem-hook"
HOST_FLAG = "--host"
CLAUDE_SETTINGS = pathlib.Path("~/.claude/settings.json")
CODEX_SETTINGS = pathlib.Path("~/.codex/hooks.json")
MUSE_SETTINGS = pathlib.Path("muse/settings.json")
HOOKS_KEY = "hooks"
MATCHER_KEY = "matcher"
ANY_MATCHER = "*"
SKILL_PATH = pathlib.Path("skills") / "agent-memory" / "SKILL.md"
SETTINGS_INDENT = 2

SETTINGS_FOR = {
    moments.HOST_CLAUDE_CODE: CLAUDE_SETTINGS,
    moments.HOST_CODEX: CODEX_SETTINGS,
    moments.HOST_MUSE_CODE: MUSE_SETTINGS,
}
ALIASES = {"muse": moments.HOST_MUSE_CODE}
MUSE_SCHEMA_VERSION = 1
MUSE_BINARY = "muse"
MUSE_DATA_HOME_FLAG = "--muse-data-home"
STORE_FLAG = "--store"


def detect() -> list[str]:
    return [host for host in SETTINGS_FOR if default_settings_path(host).parent.exists()]


def canonical_host(host: str) -> str:
    canonical = ALIASES.get(host, host)
    if canonical not in SETTINGS_FOR:
        supported = ", ".join(sorted(SETTINGS_FOR))
        raise ValidationError(
            [FieldError("host", f"unsupported host {host!r}; choose {supported}")]
        )
    return canonical


def default_settings_path(host: str, environment: dict[str, str] | None = None) -> pathlib.Path:
    canonical = canonical_host(host)
    if canonical == moments.HOST_MUSE_CODE:
        env = os.environ if environment is None else environment
        fallback = pathlib.Path(env.get("HOME", "~")).expanduser() / ".config"
        config_home = pathlib.Path(env.get("XDG_CONFIG_HOME") or fallback)
        return config_home / MUSE_SETTINGS
    return SETTINGS_FOR[canonical].expanduser()


def probe(host: str) -> str:
    canonical = canonical_host(host)
    if canonical != moments.HOST_MUSE_CODE:
        return ""
    binary = MUSE_BINARY
    resolved = shutil.which(binary)
    if resolved is None:
        raise ValidationError(
            [FieldError("host", f"{canonical} requires `{binary}` on PATH; install it and retry")]
        )
    try:
        completed = subprocess.run(
            [resolved, "--version"], capture_output=True, text=True, check=False, timeout=10
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ValidationError(
            [FieldError("host", f"could not run `{binary} --version`: {error}")]
        ) from error
    version = (completed.stdout.strip() or completed.stderr.strip()).splitlines()
    if completed.returncode != 0:
        detail = version[0] if version else f"exit {completed.returncode}"
        raise ValidationError(
            [FieldError("host", f"`{binary} --version` failed: {detail}")]
        )
    return version[0] if version else "unknown"


def install(
    host: str,
    settings_path: pathlib.Path | None = None,
    store_root: pathlib.Path | None = None,
) -> pathlib.Path:
    host = canonical_host(host)
    target = settings_path or default_settings_path(host)
    target.parent.mkdir(parents=True, exist_ok=True)
    settings = _read_settings(target)
    if host == moments.HOST_MUSE_CODE:
        settings.setdefault("schema_version", MUSE_SCHEMA_VERSION)
    hooks = settings.setdefault(HOOKS_KEY, {})
    if not isinstance(hooks, dict):
        raise ValidationError([FieldError("settings.hooks", "must be an object")])
    entry = {
        MATCHER_KEY: ANY_MATCHER,
        HOOKS_KEY: [{"type": "command", "command": hook_command(host, store_root)}],
    }
    for event in moments.DIALECTS[host]:
        entries = hooks.get(event, [])
        if not isinstance(entries, list):
            raise ValidationError([FieldError(f"settings.hooks.{event}", "must be an array")])
        kept = [existing for existing in entries if not _mentions_command(existing)]
        hooks[event] = [*kept, entry]
    rendered = json.dumps(settings, indent=SETTINGS_INDENT, sort_keys=True) + "\n"
    if not target.exists() or target.read_text(encoding="utf-8") != rendered:
        _atomic_write(target, rendered)
    _install_skill(target.parent / SKILL_PATH)
    return target


def hook_command(host: str, store_root: pathlib.Path | None = None) -> str:
    """By absolute path: desktop clients run hooks without the user's shell PATH."""
    command = [str(pathlib.Path(sys.executable).parent / HOOK_COMMAND), HOST_FLAG, host]
    if host == moments.HOST_MUSE_CODE:
        command += [MUSE_DATA_HOME_FLAG, str(_muse_data_home())]
        if store_root is not None:
            command += [STORE_FLAG, str(store_root)]
    return shlex.join(command)


def _muse_data_home(environment: dict[str, str] | None = None) -> pathlib.Path:
    env = os.environ if environment is None else environment
    fallback = pathlib.Path(env.get("HOME", "~")).expanduser() / ".local" / "share"
    return pathlib.Path(env.get("XDG_DATA_HOME") or fallback)


def _install_skill(path: pathlib.Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rendered = prompts.skill()
    if not path.exists() or path.read_text(encoding="utf-8") != rendered:
        path.write_text(rendered, encoding="utf-8")


def _read_settings(target: pathlib.Path) -> dict[str, object]:
    if not target.exists():
        return {}
    try:
        parsed = json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValidationError(
            [FieldError("settings", f"malformed JSON in {target}: {error.msg}")]
        ) from error
    if not isinstance(parsed, dict):
        raise ValidationError([FieldError("settings", f"{target} must contain a JSON object")])
    return parsed


def _atomic_write(target: pathlib.Path, rendered: str) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    path = pathlib.Path(temporary)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(rendered)
            handle.flush()
            os.fsync(handle.fileno())
        path.replace(target)
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def _mentions_command(entry: object) -> bool:
    return HOOK_COMMAND in json.dumps(entry)
