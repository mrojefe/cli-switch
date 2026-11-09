"""Détection des process `agy` (Antigravity CLI) en cours d'exécution.

Contrairement à Claude Code, `agy` n'a pas de fichiers de session/lockfile
documentés à lire — on détecte simplement les process par leur nom via
psutil (cross-platform), comme le fait AntigravityManager (kill_process()
dans cli/core.py, cf. plan)."""

from __future__ import annotations

import os
import time

import psutil


def _is_desktop_language_server(name: str, cmdline: list[str]) -> bool:
    exe = (cmdline[0].rsplit("/", 1)[-1].rsplit("\\", 1)[-1] if cmdline else "").lower()
    proc_name = (name or "").lower()
    if "language_server" not in proc_name and "language_server" not in exe:
        return False
    has_override_ide = (
        ("--override_ide_name" in cmdline and "antigravity" in cmdline)
        or any(arg == "--override_ide_name=antigravity" for arg in cmdline)
    )
    has_hub_subclient = (
        ("--subclient_type" in cmdline and "hub" in cmdline)
        or any(arg == "--subclient_type=hub" for arg in cmdline)
    )
    return has_override_ide or has_hub_subclient


def list_desktop_language_server_pids() -> list[int]:
    """PIDs des process `language_server` d'Antigravity 2.0 Desktop (`--override_ide_name antigravity`
    ou `--subclient_type hub`), sans jamais inclure le process UI Electron `antigravity`."""
    uid = os.getuid() if hasattr(os, "getuid") else None
    pids: list[int] = []
    for proc in psutil.process_iter(["pid", "name", "cmdline", "uids"]):
        try:
            info = proc.info
            proc_uid = getattr(info.get("uids"), "real", None)
            if uid is not None and proc_uid is not None and proc_uid != uid:
                continue
            name = info.get("name") or ""
            cmdline = [str(x) for x in (info.get("cmdline") or [])]
            if _is_desktop_language_server(name, cmdline):
                pids.append(info["pid"])
        except (psutil.NoSuchProcess, psutil.AccessDenied, KeyError):
            continue
    return pids


def restart_desktop_language_server(timeout: float = 6.0) -> list[int]:
    """Envoie SIGTERM au process `language_server` d'Antigravity 2.0 Desktop
    (SANS toucher au process UI Electron `antigravity`), puis attend jusqu'à
    `timeout` secondes qu'Electron (`monitorLsCrashInternal`) relance un nouveau
    `language_server` avec un nouveau PID. Retourne la liste des nouveaux PIDs."""
    old_pids = list_desktop_language_server_pids()
    if not old_pids:
        return []

    old_set = set(old_pids)
    for pid in old_pids:
        try:
            psutil.Process(pid).terminate()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        current = list_desktop_language_server_pids()
        new_pids = [p for p in current if p not in old_set]
        if new_pids:
            return new_pids
        time.sleep(0.15)

    return [p for p in list_desktop_language_server_pids() if p not in old_set]


def list_agy_pids() -> list[int]:
    """PIDs de tous les process dont le nom exécutable est `agy` (ou
    `agy.exe` sur Windows)."""
    pids = []
    for proc in psutil.process_iter(["pid", "name"]):
        try:
            name = proc.info["name"] or ""
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        if name.lower() in ("agy", "agy.exe"):
            pids.append(proc.info["pid"])
    return pids


def kill_agy_processes(pids: list[int] | None = None, timeout: float = 5.0) -> list[int]:
    """Tue les process `agy` donnés (ou tous les process `agy` actifs si
    `pids` est None). Retourne la liste des PIDs effectivement tués.

    Utilise terminate() puis kill() si le process ne répond pas dans le
    délai imparti (même prudence que le pattern kill+restart observé dans
    AntigravityManager)."""
    targets = pids if pids is not None else list_agy_pids()
    killed = []
    procs = []
    for pid in targets:
        try:
            p = psutil.Process(pid)
            p.terminate()
            procs.append(p)
        except psutil.NoSuchProcess:
            continue
    _, alive = psutil.wait_procs(procs, timeout=timeout)
    for p in alive:
        try:
            p.kill()
        except psutil.NoSuchProcess:
            pass
    for pid in targets:
        if not psutil.pid_exists(pid):
            killed.append(pid)
    return killed


def any_agy_running() -> bool:
    return bool(list_agy_pids())
