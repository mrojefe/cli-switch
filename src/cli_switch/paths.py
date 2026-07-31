"""Chemins cross-platform (Linux/WSL/macOS/Windows) utilisés par cli-switch.

Les chemins Claude sont bien documentés et stables sur toutes les
plateformes (Claude Code utilise le même schéma ~/.claude partout,
respectant CLAUDE_CONFIG_DIR si défini).

Les chemins AGY (Antigravity CLI) ont été confirmés par investigation directe
sur Linux dans cette session : ~/.gemini/oauth_creds.json (legacy, non
utilisé par la session live), ~/.gemini/google_accounts.json (champ "active"
= email courant), ~/.gemini/antigravity-cli/antigravity-oauth-token (le vrai
token de session, celui qui change à chaque login réel — confirmé par
corrélation d'horodatage avec un login OAuth observé en direct).

Les chemins AGY sur Windows ne sont PAS confirmés — à valider lors du
premier test réel sur cette plateforme. En cas d'absence, lever une erreur
explicite plutôt que d'échouer silencieusement (cf. AGY_PATHS_UNVERIFIED).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# ── Répertoire de données cli-switch ────────────────────────────────────────


def cli_switch_data_dir() -> Path:
    """Répertoire de stockage des profils cli-switch, cross-platform."""
    override = os.environ.get("CLI_SWITCH_CONFIG_DIR")
    if override:
        return Path(override)
    if sys.platform == "win32":
        base = os.environ.get("APPDATA")
        if base:
            return Path(base) / "cli-switch"
    return Path.home() / ".cli-switch"


# ── Chemins Claude Code ─────────────────────────────────────────────────────


def claude_config_home() -> Path:
    """Répertoire de config Claude Code (respecte CLAUDE_CONFIG_DIR)."""
    override = os.environ.get("CLAUDE_CONFIG_DIR")
    if override:
        return Path(override)
    return Path.home() / ".claude"


def claude_credentials_path() -> Path:
    return claude_config_home() / ".credentials.json"


def claude_global_config_path() -> Path:
    """~/.claude.json — contient oauthAccount.emailAddress (compte actif)."""
    return Path.home() / ".claude.json"


def claude_sessions_dir() -> Path:
    return claude_config_home() / "sessions"


def claude_ide_lock_dir() -> Path:
    return claude_config_home() / "ide"


def claude_daemon_auth_status_path() -> Path:
    """Écrit par le démon Claude Code lui-même à chaque tentative de
    rafraîchissement OAuth ('{"status":"auth_required"|"ok",...}'). Utilisé
    pour vérifier qu'un démon relancé après un switch est bien sain, au lieu
    de supposer que `sleep(1)` suffit (course observée : 93 collisions
    "another daemon is already running" + un blocage mesuré de 45h en
    auth_required dans daemon.log, 2026-07-31)."""
    return claude_config_home() / "daemon-auth-status.json"


# ── Chemins AGY (Antigravity CLI) ───────────────────────────────────────────

AGY_PATHS_UNVERIFIED_PLATFORMS = {"win32"}


def agy_config_home() -> Path:
    override = os.environ.get("CLI_SWITCH_AGY_CONFIG_DIR")
    if override:
        return Path(override)
    return Path.home() / ".gemini"


def agy_oauth_creds_path() -> Path:
    """Fichier legacy — pas la session active, mais conservé pour parité
    avec l'existant (certains outils Gemini annexes pourraient encore le
    lire)."""
    return agy_config_home() / "oauth_creds.json"


def agy_google_accounts_path() -> Path:
    """Contient un champ "active" — s'est avéré NE PAS être mis à jour de
    façon fiable par le CLI `agy` lui-même (testé en direct le 2026-07-04 :
    resté bloqué sur l'ancien compte après un nouveau login réel). Gardé en
    lecture pour affichage/fallback uniquement — ne pas s'y fier comme
    source de vérité, cf. agy_cli_log_path()."""
    return agy_config_home() / "google_accounts.json"


def agy_cli_log_path() -> Path:
    """Log structuré d'`agy` — contient une ligne
    'OAuth: authenticated successfully as <email>' immédiatement après
    chaque login réel, confirmée exacte en direct. Source de vérité
    beaucoup plus fiable que google_accounts.json pour détecter le compte
    qui vient de se connecter."""
    return agy_config_home() / "antigravity-cli" / "cli.log"


def agy_live_token_path() -> Path:
    """Le VRAI token de session active du CLI `agy`. Confirmé par
    corrélation d'horodatage lors d'un login OAuth réel observé en direct
    (2026-07-04) : ce fichier — et lui seul — change au moment exact de
    l'authentification. `oauth_creds.json` reste inchangé pendant des
    semaines et n'est probablement pas lu par `agy` pour sa session live.
    """
    return agy_config_home() / "antigravity-cli" / "antigravity-oauth-token"


def check_agy_paths_or_raise() -> None:
    """Vérifie que les chemins AGY attendus existent, sinon lève une erreur
    explicite plutôt que d'échouer silencieusement (surtout utile sur une
    plateforme comme Windows où ces chemins n'ont jamais été vérifiés)."""
    if sys.platform in AGY_PATHS_UNVERIFIED_PLATFORMS:
        live = agy_live_token_path()
        if not live.parent.exists():
            raise RuntimeError(
                f"Chemins AGY non vérifiés sur {sys.platform!r} et introuvables "
                f"({live}). Ces chemins ont été confirmés uniquement sur Linux — "
                "il faut les valider manuellement sur cette plateforme avant "
                "d'utiliser cli-switch pour AGY ici."
            )
