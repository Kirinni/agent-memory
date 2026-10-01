"""Muse lifecycle data reaches the same host-neutral capture, distill, and read path."""

import json
import pathlib

from agent_memory.adapters import hook_entry
from agent_memory.cli.main import main
from agent_memory.core.recall import Recall


def test_muse_hook_capture_distill_and_read(store, tmp_path, monkeypatch, capsys):
    session_id = "01a0f2d9-b58a-7182-9533-386ac7edd422"
    data_home = tmp_path / "data"
    root = data_home / "muse" / "sessions" / "2026" / "09" / "30" / session_id
    root.mkdir(parents=True)
    fixture = pathlib.Path(__file__).parents[1] / "fixtures" / "muse" / "session.jsonl"
    root.joinpath("session.jsonl").write_bytes(fixture.read_bytes())

    response = hook_entry.handle(
        store,
        {
            "host": "muse-code",
            "hook_event_name": "Stop",
            "session_id": session_id,
            "transcript_path": None,
            hook_entry.KEY_MUSE_DATA_HOME: str(data_home),
        },
        launch=lambda *_: False,
    )
    assert response["pending"] == 2

    reply = json.dumps(
        {
            "type": "fact",
            "fields": {"project": "deploy", "subject": "deploy window"},
            "abstract": "The deploy window is Friday",
            "provenance": ["0"],
        }
    )
    monkeypatch.setattr(
        "agent_memory.executor.distiller.distiller", lambda config: lambda prompt: reply
    )
    assert main(["--store", str(store.root), "--json", "distill", "--session", session_id]) == 0
    report = json.loads(capsys.readouterr().out)

    assert report["distilled"][0]["batches"][0]["written"] == ["deploy-window"]
    hit = Recall(store).recall("Friday deploy window")[0]
    assert hit.name == "deploy-window"
    assert store.read(hit.name).text == ""
    assert store.read(hit.name).record.provenance
