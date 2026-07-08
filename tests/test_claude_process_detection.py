import json
import os

from cli_switch.claude import process_detection


def test_no_sessions_dir_returns_empty(fake_home):
    assert process_detection.list_sessions() == []
    assert process_detection.list_ide_instances() == []
    assert process_detection.any_claude_running() is False


def test_dead_pid_session_ignored(fake_home):
    sessions_dir = fake_home["claude_dir"] / "sessions"
    sessions_dir.mkdir()
    dead_pid = 999_999_999  # PID improbable, considéré mort
    (sessions_dir / "1.json").write_text(
        json.dumps({"pid": dead_pid, "sessionId": "x", "cwd": "/tmp", "startedAt": 0, "kind": "interactive", "entrypoint": "cli"}),
        encoding="utf-8",
    )
    assert process_detection.list_sessions() == []


def test_alive_pid_session_detected(fake_home):
    sessions_dir = fake_home["claude_dir"] / "sessions"
    sessions_dir.mkdir()
    my_pid = os.getpid()  # toujours vivant : c'est le process de test lui-même
    (sessions_dir / "1.json").write_text(
        json.dumps({"pid": my_pid, "sessionId": "x", "cwd": "/tmp", "startedAt": 0, "kind": "interactive", "entrypoint": "cli"}),
        encoding="utf-8",
    )
    sessions = process_detection.list_sessions()
    assert len(sessions) == 1
    assert sessions[0].pid == my_pid
    assert process_detection.any_claude_running() is True


def test_malformed_session_file_ignored(fake_home):
    sessions_dir = fake_home["claude_dir"] / "sessions"
    sessions_dir.mkdir()
    (sessions_dir / "broken.json").write_text("not json", encoding="utf-8")
    assert process_detection.list_sessions() == []
