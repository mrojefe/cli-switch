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

import json
import re
import time

from cli_switch.paths import (
    agy_cli_log_path,
    agy_google_accounts_path,
    agy_live_token_path,
    agy_oauth_creds_path,
)

_AUTH_LOG_RE = re.compile(r"OAuth: authenticated successfully as (\S+)")


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
    """Email du compte AGY actif. Priorité au log (source fiable, confirmée
    en direct), google_accounts.json en repli si le log est absent/trop
    ancien."""
    return _email_from_cli_log() or _email_from_google_accounts()


def live_token_exists() -> bool:
    return agy_live_token_path().exists()


def evict_live_credentials() -> dict[str, bytes]:
    """Retire temporairement le token de session ET le refresh_token legacy,
    pour empêcher `agy` de se ré-authentifier tout seul avec l'ancien
    compte. Retourne un dict {nom_logique: contenu} pour restauration en cas
    d'échec du nouveau login."""
    evicted: dict[str, bytes] = {}
    for key, path in (
        ("live_token", agy_live_token_path()),
        ("oauth_creds", agy_oauth_creds_path()),
    ):
        if path.exists():
            evicted[key] = path.read_bytes()
            path.unlink()
    return evicted


def restore_live_credentials(evicted: dict[str, bytes]) -> None:
    from cli_switch.locking import atomic_write_bytes

    paths = {"live_token": agy_live_token_path(), "oauth_creds": agy_oauth_creds_path()}
    for key, data in evicted.items():
        atomic_write_bytes(paths[key], data)


def all_credential_paths_present() -> bool:
    return agy_live_token_path().exists() and agy_google_accounts_path().exists()
