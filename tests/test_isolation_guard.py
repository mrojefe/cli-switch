"""Garde-fou d'isolation : la suite de tests ne DOIT jamais écrire dans le vrai
~/.claude.json de l'utilisateur.

Contexte (2026-07-08) : `claude_global_config_path()` = Path.home()/'.claude.json'
est autonome ET importée en direct par plusieurs modules ; un
`monkeypatch.setattr(paths, ...)` ne l'atteignait pas → lancer `pytest` écrasait
le vrai fichier avec des valeurs de test (« a@example.com »), cassant l'identité
Claude et forçant un /login. La fixture `fake_home` redirige désormais HOME ;
ce test verrouille cette garantie."""

from __future__ import annotations

import json
from pathlib import Path

from cli_switch.paths import claude_global_config_path


def test_global_config_path_is_redirected_under_fake_home(fake_home, tmp_path):
    """Le chemin résolu doit tomber dans le tmp de test, pas dans le vrai HOME."""
    p = claude_global_config_path()
    # La garantie d'isolation : le chemin est sous le tmp du test.
    assert str(p).startswith(str(tmp_path)), p
    # Et il ne pointe pas vers un ~/.claude.json hors du tmp.
    assert p != Path.home() / ".claude.json" or str(Path.home()).startswith(str(tmp_path))


def test_writing_config_stays_in_tmp(fake_home, tmp_path):
    """Écrire via le chemin direct (comme le font les helpers de test) ne doit
    toucher que le tmp — c'est exactement le scénario qui corrompait le vrai
    fichier avant le correctif HOME."""
    p = claude_global_config_path()
    p.write_text(json.dumps({"oauthAccount": {"emailAddress": "x@test.local"}}), encoding="utf-8")
    assert p.exists()
    assert str(p).startswith(str(tmp_path))
