from cli_switch.agy import process_detection


class _FakeProc:
    def __init__(self, pid, name):
        self.info = {"pid": pid, "name": name}


def test_list_agy_pids_filters_by_name(monkeypatch):
    fakes = [_FakeProc(1, "bash"), _FakeProc(2, "agy"), _FakeProc(3, "agy.exe"), _FakeProc(4, "python3")]
    monkeypatch.setattr(process_detection.psutil, "process_iter", lambda fields: fakes)
    assert sorted(process_detection.list_agy_pids()) == [2, 3]


def test_any_agy_running_false_when_none(monkeypatch):
    monkeypatch.setattr(process_detection.psutil, "process_iter", lambda fields: [])
    assert process_detection.any_agy_running() is False
