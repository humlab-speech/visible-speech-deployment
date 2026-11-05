"""Tests for vispctl/mongo.py."""

import vispctl.mongo as mongo_mod


def test_mongosh_json_passes_password_via_stdin(monkeypatch):
    calls = {}

    class FakeCompleted:
        returncode = 0
        stdout = '{"ok":1}\n'
        stderr = "Enter password: *********\n"

    calls["all"] = []

    def fake_run(cmd, **kwargs):
        calls["cmd"] = cmd
        calls["kwargs"] = kwargs
        calls["all"].append((list(cmd), kwargs))
        return FakeCompleted()

    monkeypatch.setattr(mongo_mod, "subprocess", type("S", (), {"run": staticmethod(fake_run)}))
    monkeypatch.setattr(mongo_mod, "get_mongo_password", lambda: "s3cret")
    monkeypatch.setattr(mongo_mod, "find_mongo_container", lambda: "mongo")

    result = mongo_mod.mongosh_json("db.users.countDocuments({})")

    assert result == {"ok": 1}
    # No process EVER launched may carry the password in argv — substring on
    # every element of every call (a '-p$pw' pre-flight or a mongodb:// URI
    # element would be ps-visible; checking only the last call or only whole
    # elements let both mutants survive round 2).
    for cmd, _kw in calls["all"]:
        for element in cmd:
            assert "s3cret" not in element
    assert calls["all"], "expected at least one subprocess call"
    assert "-p" in calls["cmd"]
    # ...but it must be supplied via stdin, which podman exec forwards with -i.
    assert "-i" in calls["cmd"]
    assert calls["kwargs"]["input"] == "s3cret\n"


def test_mongosh_json_raises_on_failure(monkeypatch):
    from vispctl.exceptions import MongoError

    class FakeCompleted:
        returncode = 1
        stdout = ""
        stderr = "boom"

    monkeypatch.setattr(
        mongo_mod, "subprocess", type("S", (), {"run": staticmethod(lambda cmd, **kw: FakeCompleted())})
    )
    monkeypatch.setattr(mongo_mod, "get_mongo_password", lambda: "s3cret")
    monkeypatch.setattr(mongo_mod, "find_mongo_container", lambda: "mongo")

    try:
        mongo_mod.mongosh_json("db.users.countDocuments({})")
        raise AssertionError("expected MongoError")
    except MongoError as e:
        assert "boom" in str(e)
