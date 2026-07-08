"""Détection des process `agy` (Antigravity CLI) en cours d'exécution.

Contrairement à Claude Code, `agy` n'a pas de fichiers de session/lockfile
documentés à lire — on détecte simplement les process par leur nom via
psutil (cross-platform), comme le fait AntigravityManager (kill_process()
dans cli/core.py, cf. plan)."""

from __future__ import annotations

import psutil


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
