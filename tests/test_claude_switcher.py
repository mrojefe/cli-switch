import json

import pytest

from cli_switch.claude import switcher
from cli_switch.exceptions import NoCredentialsError, ProfileNotFoundError
from cli_switch.paths import claude_credentials_path, claude_global_config_path


def _write_active_claude(email: str):
    claude_credentials_path().write_text(json.dumps({"claudeAiOauth": {"accessToken": "tok"}}), encoding="utf-8")
    claude_global_config_path().write_text(
        json.dumps({"oauthAccount": {"emailAddress": email}}), encoding="utf-8"
    )


def test_login_without_credentials_raises(fake_home, store):
    with pytest.raises(NoCredentialsError):
        switcher.login(store, "a")


def test_login_captures_email_and_files(fake_home, store):
    _write_active_claude("a@example.com")
    email = switcher.login(store, "a")
    assert email == "a@example.com"
    p = store.profile("a")
    assert p.claude_credentials.exists()
    assert p.claude_email.read_text(encoding="utf-8") == "a@example.com"
    assert store.current_claude() == "a"


def test_switch_to_unknown_profile_raises(fake_home, store):
    with pytest.raises(ProfileNotFoundError):
        switcher.switch_to(store, "nope")


def test_switch_swaps_credentials_and_identity_preserving_state(fake_home, store):
    # Compte A : credentials tok-A + identité a@ (accountUuid uidA).
    claude_credentials_path().write_text(
        json.dumps({"claudeAiOauth": {"accessToken": "tok-A"}}), encoding="utf-8"
    )
    claude_global_config_path().write_text(
        json.dumps({"oauthAccount": {"emailAddress": "a@example.com", "accountUuid": "uidA"}}),
        encoding="utf-8",
    )
    switcher.login(store, "a")

    # Compte B : credentials tok-B + identité b@ + état vivant "projects".
    claude_credentials_path().write_text(
        json.dumps({"claudeAiOauth": {"accessToken": "tok-B"}}), encoding="utf-8"
    )
    claude_global_config_path().write_text(
        json.dumps({"oauthAccount": {"emailAddress": "b@example.com", "accountUuid": "uidB"},
                    "projects": {"x": 1}}),
        encoding="utf-8",
    )
    switcher.login(store, "b")

    switcher.switch_to(store, "a")

    # 1. Les CREDENTIALS ont basculé sur A.
    creds = json.loads(claude_credentials_path().read_text(encoding="utf-8"))
    assert creds["claudeAiOauth"]["accessToken"] == "tok-A"
    # 2. L'IDENTITÉ (oauthAccount) a aussi basculé sur A — sinon Claude se
    #    croit déconnecté (bug rapporté).
    config = json.loads(claude_global_config_path().read_text(encoding="utf-8"))
    assert config["oauthAccount"]["emailAddress"] == "a@example.com"
    assert config["oauthAccount"]["accountUuid"] == "uidA"
    # 3. L'état vivant (projects) est PRÉSERVÉ (pas d'écrasement global).
    assert config.get("projects") == {"x": 1}
    assert store.current_claude() == "a"


def test_switch_to_migrated_profile_without_identity(fake_home, store):
    """Profil migré (credentials mais pas d'oauthAccount stocké) : le switch
    ne doit pas planter, et laisse ~/.claude.json inchangé côté identité."""
    p = store.profile("legacy")
    p.root.mkdir(parents=True)
    p.claude_credentials.write_text(
        json.dumps({"claudeAiOauth": {"accessToken": "tok-legacy"}}), encoding="utf-8"
    )
    claude_credentials_path().write_text(
        json.dumps({"claudeAiOauth": {"accessToken": "tok-current"}}), encoding="utf-8"
    )
    claude_global_config_path().write_text(
        json.dumps({"oauthAccount": {"emailAddress": "cur@example.com"}}), encoding="utf-8"
    )

    switcher.switch_to(store, "legacy")  # ne doit pas lever

    creds = json.loads(claude_credentials_path().read_text(encoding="utf-8"))
    assert creds["claudeAiOauth"]["accessToken"] == "tok-legacy"
    assert store.current_claude() == "legacy"


def test_login_reuses_slot_on_same_email(fake_home, store):
    _write_active_claude("a@example.com")
    switcher.login(store, "a")
    switcher.login(store, "a-bis")  # même email actif, nouveau nom de profil

    # L'ancien profil 'a' ne doit plus revendiquer cet email côté Claude
    assert store.find_profile_by_email("claude", "a@example.com") == "a-bis"
    assert not store.profile("a").claude_email.exists()
