"""Détection des instances Claude Code en cours d'exécution.

Adapté de claude-swap (src/claude_swap/process_detection.py, licence MIT) —
même mécanisme que Claude Code utilise en interne : fichiers de session PID
(~/.claude/sessions/{pid}.json) et lockfiles IDE (~/.claude/ide/{port}.lock).

C'est ce qui permet la règle de sécurité centrale : ne jamais réécrire le
credential actif tant qu'un process vivant l'utilise (cf. claude/switcher.py).
"""

from __future__ import annotations

import json
import logging
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

from cli_switch.paths import claude_config_home, claude_ide_lock_dir, claude_sessions_dir

logger = logging.getLogger(__name__)


@dataclass
class ClaudeSession:
    pid: int
    session_id: str
    cwd: str
    started_at: int
    kind: str
    entrypoint: str
    status: str | None = None


@dataclass
class IdeInstance:
    port: int
    pid: int
    ide_name: str
    workspace_folders: list[str] = field(default_factory=list)


def is_pid_alive(pid: int) -> bool:
    """Vérifie qu'un process avec ce PID est bien actif, cross-platform."""
    if pid <= 1:
        return False
    if sys.platform == "win32":
        return _is_pid_alive_windows(pid)
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except OSError:
        return False


def _is_pid_alive_windows(pid: int) -> bool:
    try:
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if handle:
            kernel32.CloseHandle(handle)
            return True
        return False
    except Exception:
        return False


def list_sessions() -> list[ClaudeSession]:
    sessions_dir = claude_sessions_dir()
    if not sessions_dir.is_dir():
        return []
    sessions = []
    for path in sessions_dir.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            pid = data["pid"]
            if not is_pid_alive(pid):
                continue
            sessions.append(
                ClaudeSession(
                    pid=pid,
                    session_id=data.get("sessionId", ""),
                    cwd=data.get("cwd", ""),
                    started_at=data.get("startedAt", 0),
                    kind=data.get("kind", ""),
                    entrypoint=data.get("entrypoint", ""),
                    status=data.get("status"),
                )
            )
        except (json.JSONDecodeError, KeyError, TypeError, OSError) as exc:
            logger.debug("Session ignorée %s: %s", path, exc)
    return sessions


def list_ide_instances() -> list[IdeInstance]:
    ide_dir = claude_ide_lock_dir()
    if not ide_dir.is_dir():
        return []
    instances = []
    for path in ide_dir.glob("*.lock"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            pid = data.get("pid")
            if pid is None or not is_pid_alive(pid):
                continue
            port = int(path.stem)
            instances.append(
                IdeInstance(
                    port=port,
                    pid=pid,
                    ide_name=data.get("ideName", "Unknown IDE"),
                    workspace_folders=data.get("workspaceFolders", []),
                )
            )
        except (json.JSONDecodeError, KeyError, TypeError, ValueError, OSError) as exc:
            logger.debug("Lockfile IDE ignoré %s: %s", path, exc)
    return instances


def get_running_instances() -> tuple[list[ClaudeSession], list[IdeInstance]]:
    return list_sessions(), list_ide_instances()


def any_claude_running() -> bool:
    """True si au moins une instance Claude Code (CLI ou IDE) est active.

    Échoue "fermé" (retourne True) en cas d'erreur de détection : mieux vaut
    supposer qu'un process existe et refuser une écriture plutôt que
    d'écraser une session vivante par erreur."""
    try:
        sessions, ides = get_running_instances()
        return bool(sessions or ides)
    except Exception:
        logger.debug("Échec de la détection d'instances Claude", exc_info=True)
        return True
