from pingwin import __main__ as entry


def test_second_copy_is_refused(monkeypatch, tmp_path):
    monkeypatch.setattr(entry, "LOCK_FILE", tmp_path / ".pingwin.lock")
    first = entry._single_instance()
    assert first is not None
    assert entry._single_instance() is None  # already running
    first.close()
    again = entry._single_instance()  # free once the first copy exits
    assert again is not None
    again.close()
