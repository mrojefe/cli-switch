from __future__ import annotations

import base64
import json
from types import SimpleNamespace

from cli_switch.agy import credentials


_REAL_GET_KEYRING_TOKEN = credentials.get_keyring_token
_REAL_SET_KEYRING_TOKEN = credentials.set_keyring_token
_REAL_CLEAR_KEYRING_TOKEN = credentials.clear_keyring_token
_REAL_DISCOVER_DBUS_ADDRESSES = credentials.discover_dbus_addresses


def _jwt(email: str) -> str:
    payload = base64.urlsafe_b64encode(json.dumps({"email": email}).encode()).decode().rstrip("=")
    return f"header.{payload}.signature"


def test_email_from_nested_token_id_token():
    raw = json.dumps({"token": {"id_token": _jwt("nested@example.com")}})

    assert credentials._email_from_token_raw(raw) == "nested@example.com"


def test_email_from_id_token_email_fallback():
    raw = json.dumps({"token": {"access_token": "opaque"}, "id_token_email": "live@example.com"})

    assert credentials._email_from_token_raw(raw) == "live@example.com"


def test_current_email_prefers_live_token_metadata_over_recent_log(tmp_path, monkeypatch):
    token_path = tmp_path / "antigravity-oauth-token"
    token_path.write_text(json.dumps({"id_token_email": "live@example.com"}), encoding="utf-8")
    log_path = tmp_path / "cli.log"
    log_path.write_text("OAuth: authenticated successfully as stale@example.com\n", encoding="utf-8")

    monkeypatch.setattr(credentials, "agy_live_token_path", lambda: token_path)
    monkeypatch.setattr(credentials, "agy_cli_log_path", lambda: log_path)
    monkeypatch.setattr(credentials, "get_keyring_token", lambda: None)
    monkeypatch.setattr(credentials, "agy_google_accounts_path", lambda: tmp_path / "missing.json")

    assert credentials.current_agy_email() == "live@example.com"


def test_set_keyring_token_clears_stale_entries_before_store(monkeypatch):
    calls = []

    monkeypatch.setattr(credentials.shutil, "which", lambda _name: "/usr/bin/secret-tool")

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=0, stdout="")

    monkeypatch.setattr(credentials.subprocess, "run", fake_run)
    monkeypatch.setattr(credentials, "clear_keyring_token", _REAL_CLEAR_KEYRING_TOKEN)

    assert _REAL_SET_KEYRING_TOKEN("new-token") is True
    assert calls[0][0][1] == "clear"
    assert calls[1][0][1] == "store"
    assert calls[1][1]["input"] == "new-token"


def test_discover_dbus_addresses_prefers_desktop_bus_over_ssh_env(monkeypatch):
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", "unix:path=/run/user/1000/bus")
    monkeypatch.setattr(credentials.os, "getuid", lambda: 1000)

    class _FakeProc:
        def __init__(self, pid, name, cmdline, bus, uid=1000):
            self.info = {
                "pid": pid,
                "name": name,
                "cmdline": cmdline,
                "uids": SimpleNamespace(real=uid),
            }
            self._bus = bus

        def environ(self):
            return {"DBUS_SESSION_BUS_ADDRESS": self._bus}

    procs = [
        _FakeProc(
            101,
            "node",
            ["/home/user/.antigravity-ide-server/node", "--start-server"],
            "unix:path=/run/user/1000/bus",
        ),
        _FakeProc(
            202,
            "language_server",
            [
                "/home/user/.local/share/Antigravity/resources/bin/language_server",
                "--standalone",
                "--override_ide_name",
                "antigravity",
                "--subclient_type",
                "hub",
            ],
            "unix:path=/tmp/dbus-vnc-desktop,guid=1234",
        ),
    ]
    monkeypatch.setattr(credentials.psutil, "process_iter", lambda _attrs: procs)
    monkeypatch.setattr(credentials, "_read_proc_dbus_address", lambda _pid, proc: proc.environ()["DBUS_SESSION_BUS_ADDRESS"])

    buses = _REAL_DISCOVER_DBUS_ADDRESSES()
    assert buses == [
        "unix:path=/tmp/dbus-vnc-desktop,guid=1234",
        "unix:path=/run/user/1000/bus",
    ]


def test_multi_bus_get_and_set_keyring_syncs_all_discovered_buses(monkeypatch):
    buses = [
        "unix:path=/tmp/dbus-vnc-desktop,guid=1234",
        "unix:path=/run/user/1000/bus",
    ]
    monkeypatch.setattr(credentials, "discover_dbus_addresses", lambda: buses)
    monkeypatch.setattr(credentials.shutil, "which", lambda _name: "/usr/bin/secret-tool")
    monkeypatch.setattr(credentials, "clear_keyring_token", _REAL_CLEAR_KEYRING_TOKEN)

    bus_store: dict[str, str] = {}
    recorded_envs: list[tuple[str, str]] = []

    def fake_run(args, **kwargs):
        op = args[1]
        bus = kwargs["env"]["DBUS_SESSION_BUS_ADDRESS"]
        recorded_envs.append((op, bus))
        if op == "clear":
            bus_store.pop(bus, None)
            return SimpleNamespace(returncode=0, stdout="")
        if op == "store":
            bus_store[bus] = kwargs["input"]
            return SimpleNamespace(returncode=0, stdout="")
        if op == "lookup":
            val = bus_store.get(bus, "")
            return SimpleNamespace(returncode=0 if val else 1, stdout=val)
        raise AssertionError(f"unexpected op {op}")

    monkeypatch.setattr(credentials.subprocess, "run", fake_run)

    assert _REAL_SET_KEYRING_TOKEN("desktop-synced-token") is True
    assert bus_store == {
        "unix:path=/tmp/dbus-vnc-desktop,guid=1234": "desktop-synced-token",
        "unix:path=/run/user/1000/bus": "desktop-synced-token",
    }
    assert _REAL_GET_KEYRING_TOKEN() == "desktop-synced-token"
    assert recorded_envs[-1] == ("lookup", "unix:path=/tmp/dbus-vnc-desktop,guid=1234")
