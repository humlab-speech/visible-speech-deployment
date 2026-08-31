import types

from vispctl.runner import Runner


class FakeRes:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_run_quiet(monkeypatch):
    import vispctl.runner as mod

    def fake_run(cmd, capture_output=True, text=True):
        return FakeRes(returncode=0, stdout="ok\n", stderr="")

    monkeypatch.setattr(mod, "subprocess", types.SimpleNamespace(run=fake_run))

    r = Runner()
    rc, out, err = r.run_quiet(["echo", "hi"])
    assert rc == 0
    assert out == "ok"
    assert err == ""


def test_run_flushes_stdio_before_uncaptured_subprocess(monkeypatch):
    import vispctl.runner as mod

    order = []

    class FakeStdio:
        def flush(self):
            order.append("flush")

    def fake_run(cmd, **kwargs):
        order.append("subprocess")
        return FakeRes()

    monkeypatch.setattr(mod, "subprocess", types.SimpleNamespace(run=fake_run))
    monkeypatch.setattr(mod.sys, "stdout", FakeStdio())
    monkeypatch.setattr(mod.sys, "stderr", FakeStdio())

    Runner().run(["echo", "hi"])
    # Python output (stdout+stderr) must be flushed before the child writes to the same pipe.
    assert order == ["flush", "flush", "subprocess"]


def test_run_captured_does_not_flush(monkeypatch):
    import vispctl.runner as mod

    order = []

    class FakeStdio:
        def flush(self):
            order.append("flush")

    def fake_run(cmd, **kwargs):
        order.append("subprocess")
        return FakeRes()

    monkeypatch.setattr(mod, "subprocess", types.SimpleNamespace(run=fake_run))
    monkeypatch.setattr(mod.sys, "stdout", FakeStdio())
    monkeypatch.setattr(mod.sys, "stderr", FakeStdio())

    Runner().run(["echo", "hi"], capture=True)
    assert order == ["subprocess"]


def test_systemctl(monkeypatch):
    # Replace Runner.run at the class level so systemctl uses our stub
    def fake_run(self, cmd, capture=False, check=True):
        return FakeRes(returncode=0, stdout="active\n", stderr="")

    monkeypatch.setattr(Runner, "run", fake_run)

    r = Runner()
    res = r.systemctl("is-active", "mongo")
    assert res.returncode == 0
    assert res.stdout.strip() == "active"
