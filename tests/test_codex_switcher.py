import base64
import json

import pytest

from cli_switch.codex import switcher
from cli_switch.exceptions import NoCredentialsError, ProfileNotFoundError
from cli_switch.paths import codex_auth_path


def _make_dummy_jwt(email: str) -> str:
    header = base64.urlsafe_b64encode(b'{"alg":"none"}').decode("utf-8").rstrip("=")
    payload = base64.urlsafe_b64encode(json.dumps({"email": email}).encode("utf-8")).decode("utf-8").rstrip("=")
    return f"{header}.{payload}.sig"


def _write_active_codex(email: str):
    jwt = _make_dummy_jwt(email)
    auth_data = {
        "auth_mode": "chatgpt",
        "tokens": {
            "id_token": jwt,
            "access_token": jwt,
            "refresh_token": f"rt_{email}",
            "account_id": f"acc_{email}",
        },
    }
    codex_auth_path().parent.mkdir(parents=True, exist_ok=True)
    codex_auth_path().write_text(json.dumps(auth_data), encoding="utf-8")


def test_codex_capture_without_credentials_raises(fake_home, store):
    with pytest.raises(NoCredentialsError):
        switcher.capture_current(store, "profile_a")


def test_codex_capture_saves_files_and_email(fake_home, store):
    _write_active_codex("user1@example.com")
    email = switcher.capture_current(store, "profile_a")
    assert email == "user1@example.com"
    p = store.profile("profile_a")
    assert p.codex_auth.exists()
    assert p.codex_email.exists()
    assert p.codex_email.read_text(encoding="utf-8") == "user1@example.com"


def test_codex_switch_to_unknown_profile_raises(fake_home, store):
    with pytest.raises(ProfileNotFoundError):
        switcher.switch(store, "non_existent")


def test_codex_switch_swaps_credentials(fake_home, store):
    # Setup profile A
    _write_active_codex("user_a@example.com")
    switcher.capture_current(store, "profile_a")
    store.set_current_codex("profile_a")

    # Setup profile B
    _write_active_codex("user_b@example.com")
    switcher.capture_current(store, "profile_b")
    store.set_current_codex("profile_b")

    # Switch back to A
    switcher.switch(store, "profile_a")
    assert store.current_codex() == "profile_a"
    active_auth = json.loads(codex_auth_path().read_text(encoding="utf-8"))
    assert active_auth["tokens"]["refresh_token"] == "rt_user_a@example.com"

    # Switch to B
    switcher.switch(store, "profile_b")
    assert store.current_codex() == "profile_b"
    active_auth = json.loads(codex_auth_path().read_text(encoding="utf-8"))
    assert active_auth["tokens"]["refresh_token"] == "rt_user_b@example.com"
