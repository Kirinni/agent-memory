"""Live Muse A/B/C compatibility probe using only disposable settings, workspace and Store."""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import tempfile

from agent_memory.core.store import Store

SENTINEL = "muse-sandbox-probe-7f31"
TIMEOUT_SECONDS = 240
AUTH_MARKERS = ("missing meta credentials", "muse login", "model api onboarding")


def _run(
    command: list[str], environment: dict[str, str], cwd: pathlib.Path
) -> subprocess.CompletedProcess:
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
        timeout=TIMEOUT_SECONDS,
        env=environment,
        cwd=cwd,
    )


def _blocked_by_auth(*runs: subprocess.CompletedProcess) -> bool:
    output = "\n".join(f"{run.stdout}\n{run.stderr}" for run in runs).lower()
    return any(marker in output for marker in AUTH_MARKERS)


def _error(run: subprocess.CompletedProcess) -> str:
    return (run.stderr.strip() or run.stdout.strip())[-400:]


def _hook_trace(data_home: pathlib.Path) -> list[dict[str, object]]:
    found = []
    for path in data_home.glob("muse/sessions/*/*/*/*/session.jsonl"):
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            payload = record.get("payload", {}) if isinstance(record, dict) else {}
            event = payload.get("event", {}) if isinstance(payload, dict) else {}
            if isinstance(event, dict) and event.get("kind") == "hook_run_terminal":
                found.append(
                    {
                        key: event.get(key)
                        for key in ("event", "status", "exit_code", "error", "stderr")
                    }
                )
    return found


def main() -> int:
    muse = shutil.which("muse")
    mem = shutil.which("mem")
    mem_hook = shutil.which("mem-hook")
    mem_mcp = shutil.which("mem-mcp")
    if not all((muse, mem, mem_hook, mem_mcp)):
        print(json.dumps({"ok": False, "error": "muse, mem, mem-hook and mem-mcp are required"}))
        return 2

    repository = pathlib.Path.cwd().resolve()
    parent_config = pathlib.Path(
        os.environ.get("XDG_CONFIG_HOME", pathlib.Path.home() / ".config")
    )
    auth_path = pathlib.Path(
        os.environ.get("MUSE_AUTH_PATH", parent_config / "muse" / "auth.json")
    )
    with tempfile.TemporaryDirectory(prefix=".muse-preflight-", dir=repository) as external_raw:
        external = pathlib.Path(external_raw)
        store_root = external / "store"
        workspace = external / "workspace"
        config_home = external / "config"
        data_home = external / "data"
        clean_home = external / "home"
        workspace.mkdir()
        config_home.joinpath("muse").mkdir(parents=True)
        data_home.mkdir()
        clean_home.mkdir()
        store = Store(store_root, agent="muse-preflight")
        store.init()
        store.record(abstract=SENTINEL, body=SENTINEL, type="fact", name="probe-seed")
        store.sync_index()

        hook_command = (
            f"{mem_hook} --host muse-code "
            f"--muse-data-home {data_home} --store {store_root}"
        )
        settings: dict[str, object] = {
            "schema_version": 1,
            "hooks": {
                event: [
                    {
                        "matcher": "*",
                        "hooks": [
                            {"type": "command", "command": hook_command, "timeout": 30}
                        ],
                    }
                ]
                for event in ("SessionStart", "Stop", "SessionEnd")
            },
        }
        settings_path = config_home / "muse" / "settings.json"
        settings_path.write_text(json.dumps(settings), encoding="utf-8")
        environment = {
            **os.environ,
            "HOME": str(clean_home),
            "XDG_CONFIG_HOME": str(config_home),
            "XDG_DATA_HOME": str(data_home),
            "MUSE_AUTH_PATH": str(auth_path),
            "MUSE_NO_AUTO_UPDATE": "1",
            "AGENT_MEMORY_STORE": str(store_root),
        }

        version = _run([str(muse), "--version"], environment, workspace)
        hook = _run(
            [str(muse), "exec", "--provider", "echo", "--workspace", str(workspace), "hook"],
            environment,
            workspace,
        )
        hook_wrote = bool(list(store.layout.sessions.glob("*.jsonl")))

        recall_prompt = (
            f"Use the shell to run exactly: mem --json recall {SENTINEL}. "
            f"Reply with {SENTINEL} if the result contains it."
        )
        shell = _run(
            [str(muse), "exec", "--json", "--workspace", str(workspace), recall_prompt],
            environment,
            workspace,
        )
        shell_read = shell.returncode == 0 and SENTINEL in shell.stdout

        settings["mcp_servers"] = {
            "agent-memory": {
                "transport": "stdio",
                "command": str(mem_mcp),
                "args": [],
                "env": {"AGENT_MEMORY_STORE": str(store_root)},
                "mode": "required",
            }
        }
        settings_path.write_text(json.dumps(settings), encoding="utf-8")
        before = len(store.records())
        mcp_prompt = (
            "Call the memory_record MCP tool exactly once with type fact, "
            f"abstract {SENTINEL}-mcp, and body {SENTINEL}-mcp."
        )
        mcp = _run(
            [str(muse), "exec", "--json", "--workspace", str(workspace), mcp_prompt],
            environment,
            workspace,
        )
        records = store.records()
        mcp_wrote = mcp.returncode == 0 and any(
            record.abstract == f"{SENTINEL}-mcp" for record in records[before:]
        )
        blocker = "BLOCKED_BY_MUSE_AUTH" if _blocked_by_auth(shell, mcp) else ""
        hook_log_path = store.layout.state_dir / "hooks.log"
        hook_log = (
            hook_log_path.read_text(encoding="utf-8")[-400:]
            if hook_log_path.exists()
            else ""
        )
        result = {
            "ok": shell_read and hook.returncode == 0 and hook_wrote and mcp_wrote,
            "blocker": blocker,
            "version": (version.stdout.strip() or version.stderr.strip()).splitlines()[:1],
            "shell_recall": shell_read,
            "shell_error": _error(shell) if shell.returncode else "",
            "hook_write": hook.returncode == 0 and hook_wrote,
            "hook_error": _error(hook) if hook.returncode else "",
            "hook_log": hook_log,
            "hook_trace": _hook_trace(data_home),
            "mcp_write": mcp_wrote,
            "mcp_error": _error(mcp) if mcp.returncode else "",
            "sandbox_disabled": False,
            "workspace_native_memory_present": (workspace / ".agents" / "memory").exists(),
            "account_native_memory_isolation": "clean_home_and_xdg",
        }
        print(json.dumps(result, sort_keys=True))
        return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
