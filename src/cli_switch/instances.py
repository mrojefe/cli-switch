"""Gestion des environnements d'instance isolés pour multi-comptes sans permutation globale.

Permet à chaque agent ou tâche en arrière-plan d'exécuter Codex ou AGY avec un profil
spécifique (ex: 'jfe', 'amine', 'meolulu') sans écraser les fichiers globaux (~/.codex ou ~/.gemini)
et sans impacter les sessions interactives en cours dans le terminal de l'utilisateur.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Dict, List, Optional

from cli_switch import paths
from cli_switch.store import Store


def instances_base_dir() -> Path:
    """Répertoire contenant toutes les instances isolées (~/.cli-switch/instances)."""
    base = paths.cli_switch_data_dir() / "instances"
    base.mkdir(parents=True, exist_ok=True)
    return base


def get_instance_dir(profile_name: str) -> Path:
    """Chemin d'une instance spécifique."""
    return instances_base_dir() / profile_name


def setup_instance(profile_name: str, store: Optional[Store] = None) -> Path:
    """Initialise ou met à jour l'arborescence d'une instance isolée pour un profil."""
    if store is None:
        store = Store(data_dir=paths.cli_switch_data_dir())
    if not store.profile_exists(profile_name):
        raise ValueError(f"Profil '{profile_name}' introuvable dans cli-switch.")

    inst_dir = get_instance_dir(profile_name)
    inst_dir.mkdir(parents=True, exist_ok=True)

    profile = store.profile(profile_name)

    # 1. Isolation Codex: auth.json dans le répertoire d'instance
    if profile.codex_auth.exists():
        target_auth = inst_dir / "auth.json"
        target_auth.unlink(missing_ok=True)
        # Lien symbolique vers le fichier auth.json du profil
        try:
            target_auth.symlink_to(profile.codex_auth.resolve())
        except OSError:
            shutil.copy2(profile.codex_auth, target_auth)

    # 2. Isolation AGY: .gemini/antigravity-cli/antigravity-oauth-token
    gemini_dir = inst_dir / ".gemini"
    agy_cli_dir = gemini_dir / "antigravity-cli"
    agy_cli_dir.mkdir(parents=True, exist_ok=True)

    if profile.agy_live_token.exists():
        target_token = agy_cli_dir / "antigravity-oauth-token"
        target_token.unlink(missing_ok=True)
        try:
            target_token.symlink_to(profile.agy_live_token.resolve())
        except OSError:
            shutil.copy2(profile.agy_live_token, target_token)

    if profile.agy_google_accounts.exists():
        target_accs = gemini_dir / "google_accounts.json"
        target_accs.unlink(missing_ok=True)
        try:
            target_accs.symlink_to(profile.agy_google_accounts.resolve())
        except OSError:
            shutil.copy2(profile.agy_google_accounts, target_accs)

    # 3. Liens transparents pour le reste du système (quand HOME=inst_dir pour AGY)
    real_home = Path.home()
    if ".cli-switch/instances" in str(real_home):
        try:
            import pwd
            real_home = Path(pwd.getpwuid(os.getuid()).pw_dir)
        except Exception:
            pass

    for link_name in [".local", ".config", ".cache", ".ssh", ".nvm"]:
        real_target = real_home / link_name
        inst_link = inst_dir / link_name
        if real_target.exists():
            if inst_link.is_symlink():
                try:
                    if inst_link.resolve() != real_target.resolve():
                        inst_link.unlink()
                        inst_link.symlink_to(real_target)
                except OSError:
                    pass
            elif not inst_link.exists():
                try:
                    inst_link.symlink_to(real_target)
                except OSError:
                    pass

    # Liens transparents pour .gemini (settings.json, plugins, bin, etc. tout en isolant le token)
    real_gemini = real_home / ".gemini"
    if real_gemini.exists():
        for item in real_gemini.iterdir():
            if item.name in ("google_accounts.json", "antigravity-cli"):
                continue
            target_link = gemini_dir / item.name
            if target_link.is_symlink():
                try:
                    if target_link.resolve() != item.resolve():
                        target_link.unlink()
                        target_link.symlink_to(item)
                except OSError:
                    pass
            elif not target_link.exists():
                try:
                    target_link.symlink_to(item)
                except OSError:
                    pass

        real_agy_cli = real_gemini / "antigravity-cli"
        if real_agy_cli.exists():
            conv_dir = agy_cli_dir / "conversations"
            if conv_dir.is_symlink():
                conv_dir.unlink()
            conv_dir.mkdir(parents=True, exist_ok=True)
            for item in real_agy_cli.iterdir():
                if item.name in ("antigravity-oauth-token", "cli.log", "log", "crashes", "conversations"):
                    continue
                target_link = agy_cli_dir / item.name
                if target_link.is_symlink():
                    try:
                        if target_link.resolve() != item.resolve():
                            target_link.unlink()
                            target_link.symlink_to(item)
                    except OSError:
                        pass
                elif not target_link.exists():
                    try:
                        target_link.symlink_to(item)
                    except OSError:
                        pass

    return inst_dir



def get_instance_env(profile_name: str, provider: str = "all") -> Dict[str, str]:
    """Retourne les variables d'environnement nécessaires pour exécuter une commande isolée."""
    inst_dir = setup_instance(profile_name)
    env = os.environ.copy()

    # Pour Codex : CODEX_HOME pointe directement vers l'instance
    env["CODEX_HOME"] = str(inst_dir)

    # Pour AGY : HOME pointe vers l'instance (contenant .gemini isolé et liens transparents)
    if provider.lower() in ("agy", "antigravity", "all"):
        env["HOME"] = str(inst_dir)
        env["CLI_SWITCH_INSTANCE_PROFILE"] = profile_name
        env.pop("DBUS_SESSION_BUS_ADDRESS", None)

    return env


def run_in_instance(profile_name: str, cmd: List[str], provider: str = "all") -> int:
    """Exécute une commande arbitraire dans l'environnement isolé du profil."""
    env = get_instance_env(profile_name, provider=provider)
    proc = subprocess.run(cmd, env=env)
    return proc.returncode
