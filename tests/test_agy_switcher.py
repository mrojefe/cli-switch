import json

import pytest

from cli_switch.agy import switcher
from cli_switch.exceptions import NoCredentialsError, ProfileNotFoundError
from cli_switch.paths import agy_google_accounts_path, agy_live_token_path


def _simulate_login(email: str, token: str = "tok"):
    """Simule ce que ferait `agy` après une authentification réussie :
    écrit le token de session + le champ "active" de google_accounts.json."""
    agy_live_token_path().write_text(token, encoding="utf-8")
    agy_google_accounts_path().write_text(json.dumps({"active": email}), encoding="utf-8")


def test_begin_login_evicts_existing_token(fake_home):
    _simulate_login("old@example.com", token="old-tok")
    session = switcher.begin_login("a")
    assert session.evicted["live_token"] == b"old-tok"
    assert not agy_live_token_path().exists()


def test_begin_login_no_existing_token(fake_home):
    session = switcher.begin_login("a")
    assert session.evicted == {}


def test_begin_login_evicts_legacy_oauth_creds_too(fake_home):
    """Le refresh_token legacy doit AUSSI être évincé, sinon `agy` se
    ré-authentifie tout seul avec l'ancien compte dès la première requête
    (bug réel reproduit et corrigé le 2026-07-04)."""
    from cli_switch.paths import agy_oauth_creds_path

    _simulate_login("old@example.com", token="old-tok")
    agy_oauth_creds_path().write_text('{"refresh_token": "legacy-rt"}', encoding="utf-8")

    session = switcher.begin_login("a")

    assert not agy_oauth_creds_path().exists()
    assert session.evicted["oauth_creds"] == b'{"refresh_token": "legacy-rt"}'


def test_complete_login_restores_on_cancelled_login(fake_home):
    _simulate_login("old@example.com", token="old-tok")
    session = switcher.begin_login("a")
    # L'utilisateur annule : aucun nouveau token n'apparaît.
    with pytest.raises(NoCredentialsError):
        switcher.complete_login(_DummyStore(), session)
    assert agy_live_token_path().read_text(encoding="utf-8") == "old-tok"


class _DummyStore:
    """Store minimal pour le test d'annulation (jamais utilisé avant l'échec)."""


def test_complete_login_captures_new_account(fake_home, store):
    session = switcher.begin_login("a")
    _simulate_login("a@example.com", token="new-tok")

    email = switcher.complete_login(store, session)

    assert email == "a@example.com"
    p = store.profile("a")
    assert p.agy_live_token.read_text(encoding="utf-8") == "new-tok"
    assert p.agy_email.read_text(encoding="utf-8") == "a@example.com"
    assert store.current_agy() == "a"


def test_switch_to_unknown_profile_raises(fake_home, store):
    with pytest.raises(ProfileNotFoundError):
        switcher.switch_to(store, "nope")


def test_switch_to_swaps_live_token(fake_home, store):
    session_a = switcher.begin_login("a")
    _simulate_login("a@example.com", token="tok-a")
    switcher.complete_login(store, session_a)

    session_b = switcher.begin_login("b")
    _simulate_login("b@example.com", token="tok-b")
    switcher.complete_login(store, session_b)

    assert agy_live_token_path().read_text(encoding="utf-8") == "tok-b"

    switcher.switch_to(store, "a")
    assert agy_live_token_path().read_text(encoding="utf-8") == "tok-a"
    assert store.current_agy() == "a"

    # Le profil 'b' doit avoir récupéré son propre token en sauvegarde avant l'écrasement
    assert store.profile("b").agy_live_token.read_text(encoding="utf-8") == "tok-b"


def test_switch_to_does_not_kill_by_default(fake_home, store, monkeypatch):
    session_a = switcher.begin_login("a")
    _simulate_login("a@example.com", token="tok-a")
    switcher.complete_login(store, session_a)

    from cli_switch.agy import process_detection

    called = {"kill": False}

    def fake_kill(*args, **kwargs):
        called["kill"] = True
        return []

    monkeypatch.setattr(process_detection, "kill_agy_processes", fake_kill)
    killed = switcher.switch_to(store, "a", kill_running=False)
    assert killed == []
    assert called["kill"] is False


def test_keyring_eviction_and_sync(fake_home, store):
    from cli_switch.agy import credentials

    # Set mock keyring token
    credentials.set_keyring_token("keyring-tok-a")
    session = switcher.begin_login("a")
    assert session.evicted["keyring"] == b"keyring-tok-a"
    assert credentials.get_keyring_token() is None

    # Simulate login creating only keyring token
    credentials.set_keyring_token("keyring-tok-new")
    switcher.complete_login(store, session, force=True)

    assert store.profile("a").agy_live_token.read_text(encoding="utf-8") == "keyring-tok-new"
    assert credentials.get_keyring_token() == "keyring-tok-new"
