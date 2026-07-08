"""Logique de bascule multi-compte pour AGY (Antigravity CLI).

Contraintes découvertes cette session (cf. plan) :
- `agy` n'a AUCUNE sous-commande login/logout/auth (vérifié exhaustivement :
  `agy --help` + recherche de flags cachés dans le binaire). Le lien
  d'authentification OAuth ne s'affiche que si `agy` démarre sans session
  valide chargée.
- Un process `agy` déjà lancé garde son identité en mémoire — swapper les
  fichiers sur le disque ne l'affecte pas rétroactivement (même limite que
  Claude Code). Contrairement à l'app IDE Antigravity (persistante, ciblée
  par AntigravityManager), notre CLI est invoqué par terminal comme `claude`
  — on ne tue donc PAS les process actifs par défaut, seulement sur demande
  explicite (kill_running=True), pour rester cohérent avec le module claude/
  et éviter une action destructrice non sollicitée.
"""

from __future__ import annotations

from dataclasses import dataclass

from cli_switch.agy import process_detection
from cli_switch.agy.credentials import (
    current_agy_email,
    evict_live_credentials,
    live_token_exists,
    restore_live_credentials,
)
from cli_switch.exceptions import NoCredentialsError, ProfileNotFoundError
from cli_switch.locking import atomic_copy, cli_switch_lock
from cli_switch.paths import (
    agy_google_accounts_path,
    agy_live_token_path,
    agy_oauth_creds_path,
)
from cli_switch.store import Store


@dataclass
class LoginSession:
    """État intermédiaire d'un login AGY en cours (éviction faite, en
    attente que l'utilisateur s'authentifie manuellement)."""

    name: str
    evicted: dict[str, bytes]


def begin_login(name: str) -> LoginSession:
    """Étape 1 du login : évince le token de session ET le refresh_token
    legacy (oauth_creds.json) — les deux sont nécessaires, sinon `agy` se
    ré-authentifie silencieusement avec l'ancien compte dès la première
    requête (bug reproduit et corrigé le 2026-07-04).

    L'appelant (cli.py) doit ensuite :
      1. Demander à l'utilisateur de lancer `agy` (sans argument) dans un
         autre terminal, taper un message pour déclencher l'auth, et
         s'authentifier via le lien affiché.
      2. Attendre confirmation.
      3. Appeler complete_login().
    """
    evicted = evict_live_credentials()
    return LoginSession(name=name, evicted=evicted)


def complete_login(store: Store, session: LoginSession, *, force: bool = False) -> str:
    """Étape 2 du login : vérifie qu'un nouveau token est apparu, capture le
    profil. Si aucun nouveau token n'est apparu, restaure l'ancien (pas de
    perte de session) et lève NoCredentialsError."""
    if not live_token_exists():
        if session.evicted:
            restore_live_credentials(session.evicted)
        raise NoCredentialsError(
            "Aucun nouveau token AGY détecté — le login a probablement été "
            "annulé. L'ancienne session a été restaurée."
        )

    email = current_agy_email()
    if not email and not force:
        raise NoCredentialsError(
            "Impossible de détecter l'email du compte AGY actif "
            f"({agy_google_accounts_path()} absent ou invalide). "
            "Utilisez force=True pour continuer sans confirmation d'identité."
        )

    with cli_switch_lock(store.lock_path):
        if email:
            existing = store.find_profile_by_email("agy", email)
            if existing and existing != session.name:
                store.clear_agy_side(existing)

        profile = store.profile(session.name)
        profile.root.mkdir(parents=True, exist_ok=True)
        atomic_copy(agy_live_token_path(), profile.agy_live_token)
        if agy_google_accounts_path().exists():
            atomic_copy(agy_google_accounts_path(), profile.agy_google_accounts)
        if agy_oauth_creds_path().exists():
            atomic_copy(agy_oauth_creds_path(), profile.agy_oauth_creds)
        if email:
            profile.agy_email.write_text(email, encoding="utf-8")
        store.set_current_agy(session.name)

    return email or ""


def switch_to(store: Store, name: str, *, kill_running: bool = False) -> list[int]:
    """Bascule AGY vers le profil `name`.

    Ne tue PAS les process `agy` actifs par défaut — ils gardent leur
    identité en mémoire jusqu'à ce que l'utilisateur les relance lui-même.
    Passer kill_running=True pour les tuer explicitement avant le switch
    (utile si l'utilisateur veut forcer un redémarrage immédiat).

    Retourne la liste des PIDs tués (vide si kill_running=False ou aucun
    process actif).
    """
    if not store.profile_exists(name):
        raise ProfileNotFoundError(f"Profil AGY '{name}' introuvable.")
    profile = store.profile(name)
    if not profile.agy_live_token.exists():
        raise ProfileNotFoundError(
            f"Le profil '{name}' n'a pas de session AGY enregistrée. "
            f"Utilisez 'cli-switch agy login {name}' d'abord."
        )

    killed: list[int] = []
    if kill_running:
        killed = process_detection.kill_agy_processes()

    with cli_switch_lock(store.lock_path):
        current_name = store.current_agy()
        live_path = agy_live_token_path()
        if current_name and current_name != name and live_path.exists():
            current_profile = store.profile(current_name)
            atomic_copy(live_path, current_profile.agy_live_token)
            if agy_google_accounts_path().exists():
                atomic_copy(agy_google_accounts_path(), current_profile.agy_google_accounts)

        atomic_copy(profile.agy_live_token, live_path)
        if profile.agy_google_accounts.exists():
            atomic_copy(profile.agy_google_accounts, agy_google_accounts_path())
        if profile.agy_oauth_creds.exists():
            atomic_copy(profile.agy_oauth_creds, agy_oauth_creds_path())
        store.set_current_agy(name)

    return killed
