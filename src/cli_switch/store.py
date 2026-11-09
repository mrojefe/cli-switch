"""Registre des profils cli-switch — un profil nommé regroupe les
credentials Claude et/ou AGY pour un même "identité logique" choisie par
l'utilisateur (ex: "pro1", "ajl").

Structure sur disque (sous cli_switch_data_dir()) :

    profiles/
      <nom>/
        claude_credentials.json   (copie de ~/.claude/.credentials.json)
        claude_config.json        (copie partielle de ~/.claude.json)
        claude.email
        agy_oauth_creds.json      (copie de oauth_creds.json)
        agy_google_accounts.json  (copie de google_accounts.json)
        agy_live_token            (copie de antigravity-cli/antigravity-oauth-token)
        agy.email
    current_claude   (nom du profil actif côté Claude, ou absent)
    current_agy      (nom du profil actif côté AGY, ou absent)
    .lock
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from cli_switch.paths import cli_switch_data_dir


@dataclass(frozen=True)
class ProfilePaths:
    root: Path

    @property
    def claude_credentials(self) -> Path:
        return self.root / "claude_credentials.json"

    @property
    def claude_config(self) -> Path:
        return self.root / "claude_config.json"

    @property
    def claude_oauth_account(self) -> Path:
        # Snapshot du bloc oauthAccount de ~/.claude.json (identité :
        # accountUuid, organizationUuid, email…). Nécessaire au switch : le
        # Claude moderne se considère déconnecté si l'identité de
        # ~/.claude.json ne correspond pas au compte des credentials swappés.
        return self.root / "claude_oauth_account.json"

    @property
    def claude_email(self) -> Path:
        return self.root / "claude.email"

    @property
    def agy_oauth_creds(self) -> Path:
        return self.root / "agy_oauth_creds.json"

    @property
    def agy_google_accounts(self) -> Path:
        return self.root / "agy_google_accounts.json"

    @property
    def agy_live_token(self) -> Path:
        return self.root / "agy_live_token"

    @property
    def agy_email(self) -> Path:
        return self.root / "agy.email"

    @property
    def codex_auth(self) -> Path:
        return self.root / "codex_auth.json"

    @property
    def codex_email(self) -> Path:
        return self.root / "codex.email"


class Store:
    """Accès au registre de profils. Une seule instance par process."""

    # Correspondance entre les noms de fichiers utilisés par l'ancien
    # switcher bash (~/Desktop/ARCHIVE/claude-account-switch) et les noms du
    # nouveau format Python. Confirmé en lisant bin/cli-switch (bash) :
    # crédentials Claude stockées sous "claude.json", AGY sous
    # "agy_oauth.json"/"agy_google.json"/"agy_live_token". Sans cette
    # migration, les profils déjà enregistrés (comptes Claude/AGY déjà
    # connectés par l'utilisateur) ne seraient pas reconnus par le nouvel
    # outil, qui redemanderait un login inutilement.
    _LEGACY_FILENAME_MAP = {
        "claude.json": "claude_credentials.json",
        "agy_oauth.json": "agy_oauth_creds.json",
        "agy_google.json": "agy_google_accounts.json",
        # Identiques dans les deux formats, listés pour être exhaustif :
        # "claude.email", "agy.email", "agy_live_token".
    }

    def __init__(self, data_dir: Path | None = None) -> None:
        self.data_dir = data_dir or cli_switch_data_dir()
        self.profiles_dir = self.data_dir / "profiles"
        self.lock_path = self.data_dir / ".lock"
        self.current_claude_path = self.data_dir / "current_claude"
        self.current_agy_path = self.data_dir / "current_agy"
        self.current_codex_path = self.data_dir / "current_codex"
        self.profiles_dir.mkdir(parents=True, exist_ok=True)
        self._migrate_legacy_filenames()

    def _migrate_legacy_filenames(self) -> None:
        """Renomme les fichiers de l'ancien format bash vers le nouveau nom
        Python, pour chaque profil existant. Idempotent et non destructif :
        ne fait rien si le nouveau nom existe déjà."""
        if not self.profiles_dir.is_dir():
            return
        for profile_dir in self.profiles_dir.iterdir():
            if not profile_dir.is_dir():
                continue
            for old_name, new_name in self._LEGACY_FILENAME_MAP.items():
                old_path = profile_dir / old_name
                new_path = profile_dir / new_name
                if old_path.exists() and not new_path.exists():
                    old_path.rename(new_path)

    def profile(self, name: str) -> ProfilePaths:
        return ProfilePaths(self.profiles_dir / name)

    def list_profiles(self) -> list[str]:
        if not self.profiles_dir.is_dir():
            return []
        return sorted(p.name for p in self.profiles_dir.iterdir() if p.is_dir())

    def profile_exists(self, name: str) -> bool:
        return self.profile(name).root.is_dir()

    def read_current(self, path: Path) -> str | None:
        if not path.exists():
            return None
        value = path.read_text(encoding="utf-8").strip()
        return value or None

    def current_claude(self) -> str | None:
        return self.read_current(self.current_claude_path)

    def current_agy(self) -> str | None:
        return self.read_current(self.current_agy_path)

    def current_codex(self) -> str | None:
        return self.read_current(self.current_codex_path)

    def set_current_claude(self, name: str) -> None:
        from cli_switch.locking import atomic_write_text

        atomic_write_text(self.current_claude_path, name)

    def set_current_agy(self, name: str) -> None:
        from cli_switch.locking import atomic_write_text

        atomic_write_text(self.current_agy_path, name)

    def set_current_codex(self, name: str) -> None:
        from cli_switch.locking import atomic_write_text

        atomic_write_text(self.current_codex_path, name)

    def find_profile_by_email(self, tool: str, email: str) -> str | None:
        """Cherche un profil existant portant cet email pour l'outil donné
        ('claude', 'agy' ou 'codex'), pour éviter les doublons silencieux."""
        for name in self.list_profiles():
            p = self.profile(name)
            if tool == "claude":
                email_file = p.claude_email
            elif tool == "agy":
                email_file = p.agy_email
            elif tool == "codex":
                email_file = p.codex_email
            else:
                continue
            if email_file.exists() and email_file.read_text(encoding="utf-8").strip() == email:
                return name
        return None

    def remove_profile(self, name: str) -> None:
        import shutil

        root = self.profile(name).root
        if root.exists():
            shutil.rmtree(root)

    def clear_claude_side(self, name: str) -> None:
        """Supprime uniquement les fichiers Claude d'un profil (garde AGY
        intact si présent) — utilisé quand un nouveau login réutilise
        l'email d'un profil existant sous un autre nom."""
        p = self.profile(name)
        for f in (p.claude_credentials, p.claude_config, p.claude_email):
            f.unlink(missing_ok=True)

    def clear_agy_side(self, name: str) -> None:
        """Équivalent AGY de clear_claude_side."""
        p = self.profile(name)
        for f in (p.agy_oauth_creds, p.agy_google_accounts, p.agy_live_token, p.agy_email):
            f.unlink(missing_ok=True)

    def clear_codex_side(self, name: str) -> None:
        """Équivalent Codex de clear_claude_side."""
        p = self.profile(name)
        for f in (p.codex_auth, p.codex_email):
            f.unlink(missing_ok=True)

    def has_any_side(self, name: str) -> bool:
        p = self.profile(name)
        return p.claude_credentials.exists() or p.agy_live_token.exists() or p.codex_auth.exists()

    def remove_profile_if_empty(self, name: str) -> None:
        """Supprime le dossier du profil s'il ne reste plus aucun fichier
        (utilisé après un `clear_*_side` pour ne pas laisser un profil
        fantôme vide sur le disque)."""
        root = self.profile(name).root
        if root.is_dir() and not any(root.iterdir()):
            root.rmdir()

    def clear_all(self) -> None:
        """Supprime TOUS les profils et l'état courant (Claude + AGY + Codex)."""
        import shutil

        if self.profiles_dir.is_dir():
            shutil.rmtree(self.profiles_dir)
            self.profiles_dir.mkdir(parents=True, exist_ok=True)
        self.current_claude_path.unlink(missing_ok=True)
        self.current_agy_path.unlink(missing_ok=True)
        self.current_codex_path.unlink(missing_ok=True)

    def rename_profile(self, old: str, new: str) -> None:
        old_root = self.profile(old).root
        new_root = self.profile(new).root
        if new_root.exists():
            raise FileExistsError(f"Le profil '{new}' existe déjà.")
        old_root.rename(new_root)
        for cur_path, setter in (
            (self.current_claude_path, self.set_current_claude),
            (self.current_agy_path, self.set_current_agy),
        ):
            if self.read_current(cur_path) == old:
                setter(new)
