"""Lecture/écriture des credentials Claude Code.

~/.claude/.credentials.json  : le token OAuth actif (claudeAiOauth).
~/.claude.json               : config globale, contient oauthAccount.emailAddress.
"""

from __future__ import annotations

import json
from pathlib import Path

from cli_switch.paths import claude_credentials_path, claude_global_config_path


def read_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None


def current_claude_email() -> str | None:
    """Email du compte Claude actuellement chargé, lu depuis ~/.claude.json."""
    data = read_json(claude_global_config_path())
    if not data:
        return None
    oauth = data.get("oauthAccount", {})
    email = oauth.get("emailAddress")
    return email or None


def read_oauth_account() -> dict | None:
    """Le bloc `oauthAccount` de ~/.claude.json (identité : accountUuid,
    organizationUuid, emailAddress…). C'est ce que le Claude moderne
    cross-vérifie avec les credentials : sans lui à jour après un swap de
    token, Claude se considère déconnecté."""
    data = read_json(claude_global_config_path())
    if not data:
        return None
    oauth = data.get("oauthAccount")
    return oauth if isinstance(oauth, dict) and oauth else None


def apply_oauth_account(oauth_account: dict) -> None:
    """Réinjecte `oauthAccount` dans ~/.claude.json en préservant TOUT le
    reste du fichier (projets, historique, onboarding). Écriture atomique.
    C'est la moitié « identité » du switch, complément du swap de credentials.
    """
    from cli_switch.locking import atomic_write_text

    path = claude_global_config_path()
    data = read_json(path) or {}
    data["oauthAccount"] = oauth_account
    atomic_write_text(path, json.dumps(data, indent=2))


def credentials_exist() -> bool:
    return claude_credentials_path().exists()
