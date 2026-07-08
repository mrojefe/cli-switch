from cli_switch.store import Store


def test_profile_lifecycle(store: Store):
    assert store.list_profiles() == []
    assert not store.profile_exists("a")

    p = store.profile("a")
    p.root.mkdir(parents=True)
    p.claude_email.write_text("a@example.com", encoding="utf-8")

    assert store.profile_exists("a")
    assert store.list_profiles() == ["a"]
    assert store.find_profile_by_email("claude", "a@example.com") == "a"
    assert store.find_profile_by_email("claude", "nope@example.com") is None
    assert store.find_profile_by_email("agy", "a@example.com") is None  # mauvais outil


def test_current_tracking(store: Store):
    assert store.current_claude() is None
    store.set_current_claude("a")
    assert store.current_claude() == "a"
    assert store.current_agy() is None


def test_clear_claude_side_keeps_agy(store: Store):
    p = store.profile("a")
    p.root.mkdir(parents=True)
    p.claude_email.write_text("a@example.com", encoding="utf-8")
    p.claude_credentials.write_text("{}", encoding="utf-8")
    p.agy_email.write_text("a@example.com", encoding="utf-8")

    store.clear_claude_side("a")

    assert not p.claude_email.exists()
    assert not p.claude_credentials.exists()
    assert p.agy_email.exists()  # AGY intact


def test_rename_profile_updates_current(store: Store):
    p = store.profile("a")
    p.root.mkdir(parents=True)
    store.set_current_claude("a")

    store.rename_profile("a", "b")

    assert not store.profile_exists("a")
    assert store.profile_exists("b")
    assert store.current_claude() == "b"


def test_rename_to_existing_raises(store: Store):
    store.profile("a").root.mkdir(parents=True)
    store.profile("b").root.mkdir(parents=True)
    import pytest

    with pytest.raises(FileExistsError):
        store.rename_profile("a", "b")


def test_remove_profile(store: Store):
    store.profile("a").root.mkdir(parents=True)
    assert store.profile_exists("a")
    store.remove_profile("a")
    assert not store.profile_exists("a")
