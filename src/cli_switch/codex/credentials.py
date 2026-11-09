"""Lecture et écriture des credentials OpenAI Codex.

~/.codex/auth.json : fichier d'authentification principal contenant
les tokens OAuth (id_token, access_token, refresh_token, account_id).
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

from cli_switch.paths import codex_auth_path


def read_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None


def extract_email_from_jwt(token: str) -> str | None:
    """Extrait l'email depuis le payload d'un JWT sans vérification crypto."""
    if not token or "." not in token:
        return None
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return None
        payload_b64 = parts[1]
        padded = payload_b64 + "=" * ((4 - len(payload_b64) % 4) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded))
        email = data.get("email")
        if email and isinstance(email, str):
            return email
        # Fallback aux claims de profil imbriqués
        profile = data.get("https://api.openai.com/profile", {})
        if isinstance(profile, dict) and profile.get("email"):
            return str(profile["email"])
    except Exception:
        pass
    return None


def current_codex_email() -> str | None:
    """Email du compte Codex actuellement chargé dans ~/.codex/auth.json."""
    data = read_json(codex_auth_path())
    if not data:
        return None
    # 1. Email explicite au premier niveau
    if data.get("email"):
        return str(data["email"])
    # 2. Dans les tokens
    tokens = data.get("tokens")
    if isinstance(tokens, dict):
        # Tenter id_token
        id_token = tokens.get("id_token")
        if id_token and isinstance(id_token, str):
            email = extract_email_from_jwt(id_token)
            if email:
                return email
        # Tenter access_token
        access_token = tokens.get("access_token")
        if access_token and isinstance(access_token, str):
            email = extract_email_from_jwt(access_token)
            if email:
                return email
    return None


def credentials_exist() -> bool:
    return codex_auth_path().exists()
