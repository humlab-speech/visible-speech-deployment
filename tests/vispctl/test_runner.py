from vispctl.runner import Runner
import types


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


def test_systemctl(monkeypatch):
    # Replace Runner.run at the class level so systemctl uses our stub
    def fake_run(self, cmd, capture=False, check=True):
        return FakeRes(returncode=0, stdout="active\n", stderr="")

    monkeypatch.setattr(Runner, "run", fake_run)

    r = Runner()
    res = r.systemctl("is-active", "mongo")
    assert res.returncode == 0
    assert res.stdout.strip() == "active"
