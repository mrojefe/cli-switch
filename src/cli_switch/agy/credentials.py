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
import re
import shutil
import subprocess
import time

from cli_switch.paths import (
    agy_cli_log_path,
    agy_google_accounts_path,
    agy_live_token_path,
    agy_oauth_creds_path,
)

_AUTH_LOG_RE = re.compile(r"OAuth: authenticated successfully as (\S+)")


def get_keyring_token() -> str | None:
    """Récupère le token stocké dans le trousseau système (secret-tool)."""
    if not shutil.which("secret-tool"):
        return None
    try:
        res = subprocess.run(
            ["secret-tool", "lookup", "service", "gemini", "username", "antigravity"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        if res.returncode == 0 and res.stdout.strip():
            return res.stdout.strip()
    except Exception:
        pass
    return None


def set_keyring_token(token: str) -> bool:
    """Enregistre le token dans le trousseau système (secret-tool)."""
    if not shutil.which("secret-tool"):
        return False
    try:
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
            input=token,
            text=True,
            capture_output=True,
            timeout=3,
        )
        return res.returncode == 0
    except Exception:
        return False


def clear_keyring_token() -> bool:
    """Supprime le token du trousseau système (secret-tool)."""
    if not shutil.which("secret-tool"):
        return False
    try:
        res = subprocess.run(
            ["secret-tool", "clear", "service", "gemini", "username", "antigravity"],
            capture_output=True,
            timeout=3,
        )
        return res.returncode == 0
    except Exception:
        return False


def _email_from_token_raw(raw: str) -> str | None:
    """Extrait l'email depuis les claims du JWT 'id_token' dans le JSON de session."""
    try:
        data = json.loads(raw)
        id_token = data.get("id_token")
        if id_token and isinstance(id_token, str):
            parts = id_token.split(".")
            if len(parts) >= 2:
                payload = parts[1]
                payload += "=" * ((4 - len(payload) % 4) % 4)
                claims = json.loads(base64.urlsafe_b64decode(payload))
                return claims.get("email")
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
    kt = get_keyring_token()
    if kt:
        evicted["keyring"] = kt.encode("utf-8")
        clear_keyring_token()
    return evicted


def restore_live_credentials(evicted: dict[str, bytes]) -> None:
    from cli_switch.locking import atomic_write_bytes

    paths = {"live_token": agy_live_token_path(), "oauth_creds": agy_oauth_creds_path()}
    for key, data in evicted.items():
        if key in paths:
            atomic_write_bytes(paths[key], data)
        elif key == "keyring":
            set_keyring_token(data.decode("utf-8"))


def all_credential_paths_present() -> bool:
    return (agy_live_token_path().exists() or (get_keyring_token() is not None)) and agy_google_accounts_path().exists()

