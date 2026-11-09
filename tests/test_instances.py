"""Tests pour la gestion des instances isolées multi-comptes."""

from __future__ import annotations

import json
from pathlib import Path
import pytest

from cli_switch.instances import get_instance_dir, get_instance_env, instances_base_dir, setup_instance
from cli_switch.store import Store


def test_instances_base_dir(fake_home):
    base = instances_base_dir()
    assert base.exists()
    assert base == fake_home["data_dir"] / "instances"


def test_setup_instance_missing_profile(fake_home):
    with pytest.raises(ValueError, match="introuvable"):
        setup_instance("nonexistent_profile_xyz")


def test_setup_instance_codex_and_agy(fake_home):
    store = Store(data_dir=fake_home["data_dir"])
    prof = store.profile("testprof")

    # Create fake codex auth
    prof.codex_auth.parent.mkdir(parents=True, exist_ok=True)
    prof.codex_auth.write_text(json.dumps({"token": "codex-secret-123"}), encoding="utf-8")

    # Create fake agy token
    prof.agy_live_token.parent.mkdir(parents=True, exist_ok=True)
    prof.agy_live_token.write_text("agy-secret-token", encoding="utf-8")

    inst_path = setup_instance("testprof")
    assert inst_path.exists()
    assert inst_path == get_instance_dir("testprof")

    # Codex auth.json
    inst_auth = inst_path / "auth.json"
    assert inst_auth.exists()
    assert json.loads(inst_auth.read_text(encoding="utf-8")) == {"token": "codex-secret-123"}

    # AGY token
    inst_agy_token = inst_path / ".gemini" / "antigravity-cli" / "antigravity-oauth-token"
    assert inst_agy_token.exists()
    assert inst_agy_token.read_text(encoding="utf-8") == "agy-secret-token"


def test_get_instance_env(fake_home):
    store = Store(data_dir=fake_home["data_dir"])
    prof = store.profile("envprof")
    prof.codex_auth.parent.mkdir(parents=True, exist_ok=True)
    prof.codex_auth.write_text("{}", encoding="utf-8")

    env = get_instance_env("envprof", provider="all")
    inst_dir = str(get_instance_dir("envprof"))
    assert env["CODEX_HOME"] == inst_dir
    assert env["HOME"] == inst_dir
    assert env["CLI_SWITCH_INSTANCE_PROFILE"] == "envprof"

    # Codex only provider
    env_codex = get_instance_env("envprof", provider="codex")
    assert env_codex["CODEX_HOME"] == inst_dir
    assert "CLI_SWITCH_INSTANCE_PROFILE" not in env_codex
