"""Lecture/écriture des credentials AGY (Antigravity CLI).

Deux fichiers doivent être évincés ensemble pour forcer un nouveau login
(confirmé par un test réel le 2026-07-04 — correction d'une hypothèse fausse
de la session précédente) :

- antigravity-cli/antigravity-oauth-token : le token de session active.
- oauth_creds.json : contient un `refresh_token` toujours valide. Si on
  n'évince QUE le token de session, `agy` détecte l'absence de session au
  démarrage ("not signed in"), mais dès la première requête envoyée, il se
  ré-authentifie SILENCIEUSEMENT via ce refresh_token — recréant le fichier
  de session live avec le MÊME ancien compte, sans jamais afficher de lien.
  C'est exactement le comportement observé et reproduit : token évincé à
  17:20, réapparu de lui-même à 18:51 avec le même compte qu'avant.

google_accounts.json (champ "active") n'a pas besoin d'être évincé : ce
n'est qu'un pointeur informatif, pas une source de ré-authentification.
"""

from __future__ import annotations

import base64
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

import psutil

from cli_switch.paths import (
    agy_cli_log_path,
    agy_desktop_jetski_token_path,
    agy_desktop_oauth_token_path,
    agy_google_accounts_path,
    agy_live_token_path,
    agy_oauth_creds_path,
)

_AUTH_LOG_RE = re.compile(r"OAuth: authenticated successfully as (\S+)")


def _read_proc_dbus_address(pid: int, proc: psutil.Process | None = None) -> str | None:
    """Lit DBUS_SESSION_BUS_ADDRESS depuis /proc/<pid>/environ (ou psutil)."""
    try:
        raw = Path(f"/proc/{pid}/environ").read_bytes()
        for entry in raw.split(b"\0"):
            if entry.startswith(b"DBUS_SESSION_BUS_ADDRESS="):
                val = entry.split(b"=", 1)[1].decode("utf-8", errors="ignore").strip()
                if val:
                    return val
    except OSError:
        pass
    if proc is not None:
        try:
            val = proc.environ().get("DBUS_SESSION_BUS_ADDRESS", "").strip()
            if val:
                return val
        except (psutil.Error, OSError, AttributeError):
            pass
    return None


def discover_dbus_addresses() -> list[str]:
    """Découvre toutes les adresses DBUS_SESSION_BUS_ADDRESS actives :
    celles des process `antigravity` / `language_server` de l'utilisateur courant
    (en priorisant le bus Desktop/VNC s'il diffère du bus SSH/systemd courant)
    ainsi que celle de l'environnement courant.
    """
    env_bus = os.environ.get("DBUS_SESSION_BUS_ADDRESS", "").strip()
    uid = os.getuid() if hasattr(os, "getuid") else None

    desktop_priority_buses: list[str] = []
    other_proc_buses: list[str] = []

    try:
        for proc in psutil.process_iter(["pid", "name", "cmdline", "uids"]):
            try:
                info = proc.info
                pids_uid = getattr(info.get("uids"), "real", None)
                if uid is not None and pids_uid is not None and pids_uid != uid:
                    continue
                name = (info.get("name") or "").lower()
                cmdline = [str(x) for x in (info.get("cmdline") or [])]
                cmd_lower = " ".join(cmdline).lower()
                if not any(
                    kw in name or kw in cmd_lower
                    for kw in ("antigravity", "language_server", "gnome-keyring-daemon", "xfce4-session")
                ):
                    continue
                bus = _read_proc_dbus_address(info["pid"], proc)
                if not bus:
                    continue
                is_desktop_hub = (
                    ("--subclient_type" in cmdline and "hub" in cmdline)
                    or ("--override_ide_name" in cmdline and "antigravity" in cmdline)
                    or (bus != env_bus)
                )
                target_list = desktop_priority_buses if is_desktop_hub else other_proc_buses
                if bus not in target_list:
                    target_list.append(bus)
            except (psutil.NoSuchProcess, psutil.AccessDenied, KeyError):
                continue
    except Exception:
        pass

    ordered: list[str] = []
    for candidate in [*desktop_priority_buses, *other_proc_buses, env_bus]:
        if candidate and candidate not in ordered:
            ordered.append(candidate)
    return ordered


def _secret_env(bus: str | None) -> dict[str, str] | None:
    if not bus:
        return None
    env = dict(os.environ)
    env["DBUS_SESSION_BUS_ADDRESS"] = bus
    return env


def _secretstorage_python() -> str | None:
    """Retourne un interpréteur Python disposant de `secretstorage` (utilisé en
    repli sur les machines Linux comme le VPS où `secret-tool` n'est pas installé
    mais `python3-secretstorage` est présent)."""
    for candidate in ("/usr/bin/python3", shutil.which("python3")):
        if candidate and Path(candidate).exists():
            return candidate
    return None


def _secretstorage_op(op: str, bus: str | None = None, token: str | None = None) -> str | bool | None:
    py = _secretstorage_python()
    if not py:
        return None if op == "lookup" else False
    script = (
        "import sys, secretstorage\n"
        "op = sys.argv[1]\n"
        "conn = secretstorage.dbus_init()\n"
        "try:\n"
        "    coll = secretstorage.get_default_collection(conn)\n"
        "    if coll.is_locked():\n"
        "        sys.exit(2)\n"
        "    items = list(coll.search_items({'service': 'gemini', 'username': 'antigravity'}))\n"
        "    if op == 'lookup':\n"
        "        if not items:\n"
        "            sys.exit(1)\n"
        "        sys.stdout.write(items[0].get_secret().decode('utf-8', errors='ignore'))\n"
        "    elif op == 'clear':\n"
        "        for it in items:\n"
        "            it.delete()\n"
        "    elif op == 'store':\n"
        "        raw = sys.stdin.read().encode('utf-8')\n"
        "        for it in items:\n"
        "            it.delete()\n"
        "        coll.create_item(\n"
        "            \"Password for 'antigravity' on 'gemini'\",\n"
        "            {'service': 'gemini', 'username': 'antigravity', 'xdg:schema': 'org.freedesktop.Secret.Generic'},\n"
        "            raw,\n"
        "            replace=True,\n"
        "        )\n"
        "finally:\n"
        "    conn.close()\n"
    )
    try:
        res = subprocess.run(
            [py, "-c", script, op],
            input=token if op == "store" else None,
            capture_output=True,
            text=True,
            timeout=3,
            env=_secret_env(bus),
        )
        if op == "lookup":
            return res.stdout.strip() if res.returncode == 0 and res.stdout.strip() else None
        return res.returncode == 0
    except Exception:
        return None if op == "lookup" else False


def _target_buses(buses: list[str | None] | None = None) -> list[str | None]:
    if buses is not None:
        return buses
    discovered = discover_dbus_addresses()
    return discovered if discovered else [None]


def get_keyring_token() -> str | None:
    """Récupère le token stocké dans le trousseau système (secret-tool ou secretstorage)
    en interrogeant tous les bus D-Bus découverts (bus Desktop en priorité)."""
    has_secret_tool = bool(shutil.which("secret-tool"))
    for bus in _target_buses():
        if has_secret_tool:
            try:
                kwargs: dict = {"capture_output": True, "text": True, "timeout": 3}
                env = _secret_env(bus)
                if env is not None:
                    kwargs["env"] = env
                res = subprocess.run(
                    ["secret-tool", "lookup", "service", "gemini", "username", "antigravity"],
                    **kwargs,
                )
                if res.returncode == 0 and res.stdout.strip():
                    return res.stdout.strip()
            except Exception:
                pass
        else:
            val = _secretstorage_op("lookup", bus=bus)
            if isinstance(val, str) and val:
                return val
    return None


def set_keyring_token(token: str) -> bool:
    """Enregistre le token dans le trousseau système sur tous les bus D-Bus actifs."""
    has_secret_tool = bool(shutil.which("secret-tool"))
    any_ok = False
    for bus in _target_buses():
        if has_secret_tool:
            try:
                clear_keyring_token([bus])
                kwargs: dict = {
                    "input": token,
                    "text": True,
                    "capture_output": True,
                    "timeout": 3,
                }
                env = _secret_env(bus)
                if env is not None:
                    kwargs["env"] = env
                res = subprocess.run(
                    [
                        "secret-tool",
                        "store",
                        "--label=Password for 'antigravity' on 'gemini'",
                        "service",
                        "gemini",
                        "username",
                        "antigravity",
                    ],
                    **kwargs,
                )
                if res.returncode == 0:
                    any_ok = True
            except Exception:
                pass
        else:
            if _secretstorage_op("store", bus=bus, token=token) is True:
                any_ok = True
    return any_ok


def clear_keyring_token(buses: list[str | None] | None = None) -> bool:
    """Supprime le token du trousseau système sur tous les bus D-Bus actifs."""
    has_secret_tool = bool(shutil.which("secret-tool"))
    any_ok = False
    for bus in _target_buses(buses):
        if has_secret_tool:
            try:
                kwargs: dict = {"capture_output": True, "timeout": 3}
                env = _secret_env(bus)
                if env is not None:
                    kwargs["env"] = env
                res = subprocess.run(
                    ["secret-tool", "clear", "service", "gemini", "username", "antigravity"],
                    **kwargs,
                )
                if res.returncode == 0:
                    any_ok = True
            except Exception:
                pass
        else:
            if _secretstorage_op("clear", bus=bus) is True:
                any_ok = True
    return any_ok


def write_live_token_files(data: bytes | str) -> None:
    """Écrit le token de session actif dans les fichiers CLI et Antigravity 2.0 Desktop."""
    from cli_switch.locking import atomic_write_bytes

    raw = data.encode("utf-8") if isinstance(data, str) else data
    for path in (
        agy_live_token_path(),
        agy_desktop_oauth_token_path(),
        agy_desktop_jetski_token_path(),
    ):
        atomic_write_bytes(path, raw)


def clear_live_token_files() -> None:
    """Supprime tous les fichiers de token OAuth actifs (CLI + Desktop)."""
    for path in (
        agy_live_token_path(),
        agy_desktop_oauth_token_path(),
        agy_desktop_jetski_token_path(),
    ):
        path.unlink(missing_ok=True)


def _email_from_token_raw(raw: str) -> str | None:
    """Extrait l'email depuis le JSON de session AGY.

    Les versions recentes d'AGY imbriquent les jetons sous ``token`` et
    exposent aussi ``id_token_email`` au premier niveau. L'ancien parseur ne
    lisait que ``id_token`` a la racine, puis retombait sur un log CLI parfois
    rattache a un autre compte.
    """
    try:
        data = json.loads(raw)
        token_data = data.get("token")
        candidates = [data.get("id_token")]
        if isinstance(token_data, dict):
            candidates.append(token_data.get("id_token"))

        for id_token in candidates:
            if not id_token or not isinstance(id_token, str):
                continue
            parts = id_token.split(".")
            if len(parts) >= 2:
                payload = parts[1]
                payload += "=" * ((4 - len(payload) % 4) % 4)
                claims = json.loads(base64.urlsafe_b64decode(payload))
                email = claims.get("email")
                if isinstance(email, str) and email:
                    return email

        email = data.get("id_token_email")
        if isinstance(email, str) and email:
            return email
    except Exception:
        pass
    return None


def _email_from_live_token_source() -> str | None:
    if agy_live_token_path().exists():
        try:
            return _email_from_token_raw(agy_live_token_path().read_text(encoding="utf-8", errors="ignore"))
        except OSError:
            pass
    kt = get_keyring_token()
    if kt:
        return _email_from_token_raw(kt)
    return None


def _email_from_cli_log(max_age_s: float = 300.0) -> str | None:
    """Email du dernier login réel, lu dans cli.log (voir paths.py pour le
    pourquoi — google_accounts.json s'est avéré peu fiable). `cli.log` est
    un symlink réécrit à chaque lancement d'`agy`, vers son fichier de log
    daté ; on ne retient le match que s'il est récent, pour éviter de
    reprendre l'email d'un très vieux process encore présent dans le
    fichier."""
    log_path = agy_cli_log_path()
    if not log_path.exists():
        return None
    try:
        if time.time() - log_path.stat().st_mtime > max_age_s:
            return None
        content = log_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    matches = _AUTH_LOG_RE.findall(content)
    return matches[-1] if matches else None


def _email_from_google_accounts() -> str | None:
    path = agy_google_accounts_path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    active = data.get("active")
    return active or None


def current_agy_email() -> str | None:
    """Email du compte AGY actif. Priorité au JWT de session / keyring,
    puis log CLI, puis google_accounts.json en repli."""
    return _email_from_live_token_source() or _email_from_cli_log() or _email_from_google_accounts()


def live_token_exists() -> bool:
    return agy_live_token_path().exists() or (get_keyring_token() is not None)


def evict_live_credentials() -> dict[str, bytes]:
    """Retire temporairement le token de session ET le refresh_token legacy
    (sur disque ET dans le trousseau système / keyring), pour empêcher `agy`
    de se ré-authentifier tout seul avec l'ancien compte. Retourne un dict
    {nom_logique: contenu} pour restauration en cas d'échec du nouveau login."""
    evicted: dict[str, bytes] = {}
    for key, path in (
        ("live_token", agy_live_token_path()),
        ("oauth_creds", agy_oauth_creds_path()),
    ):
        if path.exists():
            evicted[key] = path.read_bytes()
            path.unlink()
    for desktop_tok in (agy_desktop_oauth_token_path(), agy_desktop_jetski_token_path()):
        if desktop_tok.exists():
            if "live_token" not in evicted:
                evicted["live_token"] = desktop_tok.read_bytes()
            desktop_tok.unlink()
    kt = get_keyring_token()
    if kt:
        evicted["keyring"] = kt.encode("utf-8")
        clear_keyring_token()
    return evicted


def restore_live_credentials(evicted: dict[str, bytes]) -> None:
    from cli_switch.locking import atomic_write_bytes

    for key, data in evicted.items():
        if key == "live_token":
            write_live_token_files(data)
        elif key == "oauth_creds":
            atomic_write_bytes(agy_oauth_creds_path(), data)
        elif key == "keyring":
            set_keyring_token(data.decode("utf-8"))


def all_credential_paths_present() -> bool:
    return (agy_live_token_path().exists() or (get_keyring_token() is not None)) and agy_google_accounts_path().exists()
