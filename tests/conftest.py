"""Fixtures partagées : redirige tous les chemins (Claude, AGY, data dir)
vers un répertoire temporaire, pour ne jamais toucher aux vrais fichiers du
système pendant les tests."""

from __future__ import annotations

import pytest

from cli_switch import paths
from cli_switch.store import Store


@pytest.fixture()
def fake_home(tmp_path, monkeypatch):
    claude_home = tmp_path / "claude_home"
    claude_dir = claude_home / ".claude"
    agy_dir = tmp_path / "agy_home" / ".gemini"
    data_dir = tmp_path / "cli_switch_data"
    claude_dir.mkdir(parents=True)
    agy_dir.mkdir(parents=True)
    (agy_dir / "antigravity-cli").mkdir(parents=True)

    # DÉFENSE CRITIQUE : `claude_global_config_path()` = Path.home()/".claude.json"
    # est autonome ET importé en direct par plusieurs modules (credentials.py,
    # switcher.py, ce module de test), donc un `monkeypatch.setattr(paths, ...)`
    # ne l'atteint PAS (liaison séparée). Sans redirection de HOME, lancer la
    # suite ÉCRASE le vrai ~/.claude.json de l'utilisateur avec des valeurs de
    # test (« a@example.com ») → identité Claude corrompue, /login forcé.
    # Rediriger HOME neutralise TOUS les Path.home() non patchés d'un coup.
    monkeypatch.setenv("HOME", str(claude_home))
    monkeypatch.setenv("USERPROFILE", str(claude_home))  # équivalent Windows

    monkeypatch.setattr(paths, "claude_config_home", lambda: claude_dir)
    monkeypatch.setattr(paths, "claude_global_config_path", lambda: claude_home / ".claude.json")
    monkeypatch.setattr(paths, "agy_config_home", lambda: agy_dir)
    monkeypatch.setattr(paths, "cli_switch_data_dir", lambda: data_dir)

    return {
        "claude_dir": claude_dir,
        "agy_dir": agy_dir,
        "data_dir": data_dir,
    }


@pytest.fixture(autouse=True)
def _no_real_claude_daemon(monkeypatch):
    """Empêche les tests d'invoquer le vrai binaire `claude` (daemon stop).
    Sans ça, switch_to appellerait `claude daemon stop --any` sur la vraie
    machine — effet de bord non déterministe."""
    from cli_switch.claude import switcher

    monkeypatch.setattr(switcher, "_stop_claude_daemon", lambda: None)
    # Idem pour le redémarrage déterministe post-swap : sans ça, un test
    # lancerait le VRAI `claude daemon run` sur la machine (effet de bord non
    # déterministe, et potentiellement une course avec le vrai démon en cours
    # d'utilisation — exactement le bug que ce mécanisme corrige).
    monkeypatch.setattr(switcher, "restart_claude_daemon_and_verify", lambda *a, **k: True)

    # Ne JAMAIS déclencher un vrai `claude auth login` (interactif, navigateur)
    # depuis un test — lèverait/bloquerait la suite.
    def _no_real_login():
        raise AssertionError("run_claude_auth_login ne doit pas être appelé en test")

    monkeypatch.setattr(switcher, "run_claude_auth_login", _no_real_login)


@pytest.fixture()
def store(fake_home):
    return Store(data_dir=fake_home["data_dir"])
