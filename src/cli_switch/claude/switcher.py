"""Logique de bascule multi-compte pour Claude Code.

Trois règles CRITIQUES, apprises en reproduisant/dépassant le comportement de
l'ancien claude-switch (bash) — leur non-respect provoquait des redemandes de
login et un état "tout mélangé" (2026-07-05) :

1. **Ne pas écraser GLOBALEMENT `~/.claude.json`.** Ce fichier (~28 Ko)
   contient tout l'état vivant de Claude Code (historique projets, onboarding).
   Le remplacer par un snapshot figé casse la session. On préserve tout SAUF
   la clé `oauthAccount` (cf. règle 3).
2. **Arrêter le daemon Claude avant le swap** (`claude daemon stop --any`),
   sinon un daemon vivant réécrit les anciens credentials par-dessus les
   nouveaux (course de fichiers) → retour à l'ancien compte / redemande login.
3. **Swapper AUSSI l'identité `oauthAccount`, pas seulement les credentials.**
   Le Claude moderne (contrairement à l'époque de l'ancien bash) cross-vérifie
   que `oauthAccount` (accountUuid/organizationUuid/email dans ~/.claude.json)
   correspond au compte des credentials. Si on swappe juste les tokens sans
   mettre à jour oauthAccount, Claude voit une incohérence et se déclare
   déconnecté (« aucun compte », oblige à /login). On capture donc oauthAccount
   au login et on le réinjecte (cette clé seule) au switch. C'est LE fix qui
   manquait — l'ancien bash ne le faisait pas et n'en avait pas besoin.

Règle héritée de claude-swap : ne pas écraser silencieusement le credential
actif si un process Claude Code vivant l'utilise ; avertir et exiger une
confirmation.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass

from cli_switch.claude import process_detection
from cli_switch.claude.credentials import (
    apply_oauth_account,
    current_claude_email,
    read_json,
    read_oauth_account,
)
from cli_switch.exceptions import LiveProcessConflict, NoCredentialsError, ProfileNotFoundError
from cli_switch.locking import atomic_copy, cli_switch_lock
from cli_switch.paths import claude_credentials_path, claude_global_config_path
from cli_switch.store import Store


def run_claude_auth_login() -> None:
    """Déclenche la connexion interactive d'un NOUVEAU compte Claude
    (`claude auth login`), comme le faisait l'ancien claude-switch. L'appelant
    capture ensuite le compte connecté. Interactif : hérite du terminal (le
    navigateur/QR s'affiche). Lève RuntimeError si `claude` est absent ou si la
    connexion échoue."""
    try:
        result = subprocess.run(["claude", "auth", "login"], check=False)
    except (FileNotFoundError, OSError) as e:
        raise RuntimeError(
            f"Impossible de lancer `claude auth login` ({e}). Connecte-toi "
            "manuellement puis relance avec --current."
        ) from e
    if result.returncode != 0:
        raise RuntimeError(
            f"`claude auth login` a échoué (code {result.returncode}). "
            "Aucun compte capturé."
        )


def _stop_claude_daemon() -> None:
    """Arrête le daemon Claude en tâche de fond (best-effort). Indispensable
    avant de swapper les credentials : un daemon vivant re-persiste sinon
    l'ancien token par-dessus le nouveau. Ignore toute erreur (claude absent
    du PATH, pas de daemon lancé)."""
    try:
        subprocess.run(
            ["claude", "daemon", "stop", "--any"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=15,
            check=False,
        )
    except (FileNotFoundError, OSError, subprocess.SubprocessError):
        pass


@dataclass
class LiveProcessWarning:
    sessions: list[process_detection.ClaudeSession]
    ides: list[process_detection.IdeInstance]

    def describe(self) -> str:
        parts = []
        for s in self.sessions:
            parts.append(f"session {s.kind} (pid {s.pid}, cwd={s.cwd})")
        for i in self.ides:
            parts.append(f"IDE {i.ide_name} (pid {i.pid})")
        return "; ".join(parts) if parts else "aucun"


def check_live_conflict() -> LiveProcessWarning | None:
    sessions, ides = process_detection.get_running_instances()
    if sessions or ides:
        return LiveProcessWarning(sessions=sessions, ides=ides)
    return None


def login(store: Store, name: str, *, force: bool = False) -> str:
    """Capture les credentials Claude actuellement chargés sous le profil
    `name`. L'utilisateur doit s'être déjà connecté (claude auth login) avant
    d'appeler ceci — cette fonction ne déclenche PAS le login elle-même.

    Retourne l'email détecté.
    """
    creds_path = claude_credentials_path()
    if not creds_path.exists():
        raise NoCredentialsError(
            f"Aucun credential Claude trouvé ({creds_path}). "
            "Lancez 'claude auth login' d'abord."
        )

    email = current_claude_email()
    if not email and not force:
        raise NoCredentialsError(
            "Impossible de détecter l'email du compte Claude actif "
            f"({claude_global_config_path()} absent ou invalide). "
            "Utilisez force=True pour continuer sans confirmation d'identité."
        )

    with cli_switch_lock(store.lock_path):
        if email:
            existing = store.find_profile_by_email("claude", email)
            if existing and existing != name:
                # Même piège que l'ancien bash : le navigateur peut avoir
                # réutilisé une session existante au lieu d'un nouveau login.
                # On ne supprime que le côté Claude de l'ancien profil — son
                # côté AGY (s'il existe) reste intact.
                store.clear_claude_side(existing)

        profile = store.profile(name)
        profile.root.mkdir(parents=True, exist_ok=True)
        # On sauvegarde les credentials (tokens) ET le bloc oauthAccount
        # (identité). Le reste de ~/.claude.json (projets/historique) n'est
        # jamais capturé ni restauré — cf. règle 1 en tête de fichier.
        atomic_copy(creds_path, profile.claude_credentials)
        oauth_account = read_oauth_account()
        if oauth_account is not None:
            profile.claude_oauth_account.write_text(
                json.dumps(oauth_account, indent=2), encoding="utf-8"
            )
            # L'email d'affichage DOIT provenir de l'identité réellement
            # stockée (oauthAccount), source de vérité unique. Sinon il peut
            # diverger de l'identité après un refresh au switch (bug observé :
            # profil affiché « a@example.com » alors que l'oauthAccount portait
            # le bon email).
            email = oauth_account.get("emailAddress") or email
        if email:
            profile.claude_email.write_text(email, encoding="utf-8")
        store.set_current_claude(name)

    return email or ""


def switch_to(store: Store, name: str, *, force: bool = False) -> LiveProcessWarning | None:
    """Bascule Claude vers le profil `name`.

    Si un process Claude vivant existe, retourne un LiveProcessWarning au
    lieu d'écraser le fichier — sauf si `force=True` (l'appelant, cli.py, a
    déjà obtenu confirmation interactive).

    Lève LiveProcessConflict si conflit détecté et force=False.
    """
    if not store.profile_exists(name):
        raise ProfileNotFoundError(f"Profil Claude '{name}' introuvable.")
    profile = store.profile(name)
    if not profile.claude_credentials.exists():
        raise ProfileNotFoundError(
            f"Le profil '{name}' n'a pas de credentials Claude enregistrés."
        )

    warning = check_live_conflict()
    if warning and not force:
        raise LiveProcessConflict(
            f"Claude Code est actif ({warning.describe()}). "
            "Basculer maintenant n'affectera pas ce process déjà lancé, mais "
            "pourrait le désynchroniser si vous l'utilisez encore. "
            "Relancez avec force=True (ou confirmez côté CLI) pour continuer."
        )

    with cli_switch_lock(store.lock_path):
        # Sauvegarde de sécurité : si un profil différent est actuellement
        # actif, on récupère d'abord ses tokens (potentiellement rafraîchis)
        # avant de l'écraser. UNIQUEMENT les credentials — jamais ~/.claude.json.
        current_name = store.current_claude()
        creds_path = claude_credentials_path()
        if current_name and current_name != name and creds_path.exists():
            current_profile = store.profile(current_name)
            atomic_copy(creds_path, current_profile.claude_credentials)
            # Rafraîchir aussi l'identité stockée du profil sortant (elle a pu
            # être complétée par Claude depuis le dernier login).
            cur_oauth = read_oauth_account()
            if cur_oauth is not None:
                current_profile.claude_oauth_account.write_text(
                    json.dumps(cur_oauth, indent=2), encoding="utf-8"
                )
                # Garder l'email d'affichage synchronisé avec l'identité
                # rafraîchie (sinon il se fige et diverge au fil des switchs).
                cur_email = cur_oauth.get("emailAddress")
                if cur_email:
                    current_profile.claude_email.write_text(cur_email, encoding="utf-8")

        # Arrêter le daemon AVANT d'écrire, sinon il re-persiste l'ancien token.
        _stop_claude_daemon()

        # Swap des credentials (tokens)...
        atomic_copy(profile.claude_credentials, creds_path)
        # ...ET réinjection de l'identité (oauthAccount) dans ~/.claude.json,
        # en préservant projets/historique. Sans ça, le Claude moderne voit
        # une incohérence identité↔token et se déclare déconnecté (« aucun
        # compte »), obligeant à /login. C'est LA correction du bug rapporté.
        if profile.claude_oauth_account.exists():
            try:
                oauth_account = json.loads(
                    profile.claude_oauth_account.read_text(encoding="utf-8")
                )
                apply_oauth_account(oauth_account)
            except (json.JSONDecodeError, OSError):
                pass
        store.set_current_claude(name)

    return warning
