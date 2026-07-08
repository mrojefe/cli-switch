"""Verrouillage fichier cross-platform, pour éviter que deux `cli-switch`
concurrents ne se marchent dessus (même principe que claude-swap/locking.py,
simplifié via la librairie `filelock` plutôt que réimplémenté à la main)."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from filelock import FileLock as _FileLock
from filelock import Timeout as LockTimeout


@contextmanager
def cli_switch_lock(lock_path: Path, timeout: float = 30.0) -> Iterator[None]:
    """Verrou exclusif sur `lock_path`. Lève `LockTimeout` si occupé trop
    longtemps par un autre process cli-switch."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock = _FileLock(str(lock_path) + ".flock", timeout=timeout)
    with lock:
        yield


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """Écriture atomique : fichier temporaire dans le même dossier, puis
    rename (atomique sur un même filesystem)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".{__import__('os').getpid()}.tmp")
    tmp.write_bytes(data)
    tmp.replace(path)


def atomic_write_text(path: Path, text: str, encoding: str = "utf-8") -> None:
    atomic_write_bytes(path, text.encode(encoding))


def atomic_copy(src: Path, dst: Path) -> None:
    """Copie atomique d'un fichier existant (lecture intégrale + écriture
    atomique) — évite un fichier destination partiellement écrit en cas
    d'interruption."""
    atomic_write_bytes(dst, src.read_bytes())
