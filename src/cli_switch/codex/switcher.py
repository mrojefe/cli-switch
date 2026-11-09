"""Logique de bascule multi-compte pour OpenAI Codex CLI."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from cli_switch.codex.credentials import (
    current_codex_email,
    read_json,
)
from cli_switch.exceptions import NoCredentialsError, ProfileNotFoundError
from cli_switch.locking import atomic_copy, atomic_write_text, cli_switch_lock
from cli_switch.paths import codex_auth_path, codex_config_home
from cli_switch.store import Store


def run_codex_login() -> None:
    """Déclenche la connexion interactive d'un nouveau compte Codex (`codex login`)."""
    try:
        result = subprocess.run(["codex", "login"], check=False)
    except (FileNotFoundError, OSError) as e:
        raise RuntimeError(
            f"Impossible de lancer `codex login` ({e}). Connecte-toi manuellement "
            "puis relance avec --current."
        ) from e
    if result.returncode != 0:
        raise RuntimeError(
            f"`codex login` a échoué (code {result.returncode}). Aucun compte capturé."
        )


def capture_current(store: Store, name: str, *, force: bool = False) -> str:
    """Capture les credentials Codex actuellement présents dans ~/.codex/auth.json
    sous le profil `name`.

    Retourne l'email détecté ou une chaîne descriptive.
    """
    auth_file = codex_auth_path()
    if not auth_file.exists():
        raise NoCredentialsError(
            f"Aucun credential Codex trouvé ({auth_file}). Lance 'codex login' d'abord."
        )

    auth_data = read_json(auth_file)
    if not auth_data or not auth_data.get("tokens"):
        raise NoCredentialsError(
            f"Le fichier {auth_file} ne contient aucun token valide. Lance 'codex login' d'abord."
        )

    email = current_codex_email()
    if not email and not force:
        # Fallback sur l'account_id si l'email n'a pas pu être extrait
        tokens = auth_data.get("tokens", {})
        acc_id = tokens.get("account_id")
        if acc_id:
            email = f"account:{acc_id}"
        else:
            raise NoCredentialsError(
                "Impossible de détecter l'email ou l'identité du compte Codex actif. "
                "Utilisez force=True pour continuer sans confirmation d'identité."
            )

    with cli_switch_lock(store.lock_path):
        profile = store.profile(name)
        profile.root.mkdir(parents=True, exist_ok=True)
        atomic_copy(auth_file, profile.codex_auth)
        profile.codex_auth.chmod(0o600)
        if email:
            atomic_write_text(profile.codex_email, email)

    return email or "inconnu"


def switch(store: Store, name: str) -> None:
    """Bascule le CLI Codex vers le compte du profil `name`.

    Copie atomiquement le fichier codex_auth.json du profil vers ~/.codex/auth.json.
    """
    if not store.profile_exists(name):
        raise ProfileNotFoundError(f"Profil Codex '{name}' introuvable.")
    profile = store.profile(name)
    if not profile.codex_auth.exists():
        raise ProfileNotFoundError(
            f"Le profil '{name}' n'a pas de credentials Codex enregistrés."
        )

    with cli_switch_lock(store.lock_path):
        current_name = store.current_codex()
        auth_file = codex_auth_path()
        # Sécurité : sauvegarder les tokens actuels vers le profil actif précédent
        # au cas où ils ont été rafraîchis en cours d'utilisation
        if current_name and current_name != name and auth_file.exists():
            current_profile = store.profile(current_name)
            if current_profile.root.is_dir():
                atomic_copy(auth_file, current_profile.codex_auth)
                current_profile.codex_auth.chmod(0o600)
                cur_email = current_codex_email()
                if cur_email:
                    atomic_write_text(current_profile.codex_email, cur_email)

        # Assurer que ~/.codex existe
        codex_config_home().mkdir(parents=True, exist_ok=True)

        # Swap des credentials
        atomic_copy(profile.codex_auth, auth_file)
        auth_file.chmod(0o600)
        store.set_current_codex(name)
