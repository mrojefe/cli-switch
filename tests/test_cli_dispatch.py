"""Dispatch d'arguments : les fautes de frappe sur une commande ne doivent
JAMAIS être transformées en switch de profil cryptique (« Got unexpected extra
argument(s) »). Régression du 2026-07-08 (`cli-switch claud login jfe`)."""

from __future__ import annotations

import pytest

from cli_switch.cli import _rewrite_argv, _typo_error


# ── Fautes de frappe : erreur claire attendue ────────────────────────────────
@pytest.mark.parametrize(
    "argv, suggestion",
    [
        (["claud", "login", "jfe"], "claude login jfe"),
        (["clade", "switch", "x"], "claude switch x"),
        (["claude", "logni", "jfe"], "claude login jfe"),
        (["claude", "swich", "jfe"], "claude switch jfe"),
        (["agy", "us", "meo"], "agy use meo"),
    ],
)
def test_typo_is_caught_with_suggestion(argv, suggestion):
    err = _typo_error(argv)
    assert err is not None
    assert suggestion in err


# ── Usages légitimes : jamais interceptés ────────────────────────────────────
@pytest.mark.parametrize(
    "argv",
    [
        ["jfe"],                    # switch implicite (un seul nom)
        ["claude", "jfe"],          # switch claude implicite
        ["agy", "meo"],             # switch agy implicite
        ["claude", "switch", "jfe"],
        ["claude", "login", "jfe"],
        ["claude", "current"],
        ["list"],
        ["current"],
        ["-h"],
        ["--help"],
        ["help"],
        ["switch", "jfe"],
    ],
)
def test_legit_usage_not_flagged(argv):
    assert _typo_error(argv) is None


def test_rewrite_prepends_switch_for_bare_profile():
    assert _rewrite_argv(["jfe"]) == ["switch", "jfe"]
    assert _rewrite_argv(["claude", "jfe"]) == ["claude", "switch", "jfe"]


def test_rewrite_keeps_known_commands():
    assert _rewrite_argv(["list"]) == ["list"]
    assert _rewrite_argv(["claude", "switch", "jfe"]) == ["claude", "switch", "jfe"]
    assert _rewrite_argv(["help"]) == ["--help"]
