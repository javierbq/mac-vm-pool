from unittest.mock import MagicMock
from mac_vm_pool import human_session as hs


def _ok(stdout="", rc=0):
    m = MagicMock(); m.returncode = rc; m.stdout = stdout; m.stderr = ""
    return m


def test_deploy_app_mkdirs_then_scps():
    calls = []
    def runner(args, **kw):
        calls.append(args); return _ok()
    hs.deploy_app("10.0.0.5", "/k/key", "/tmp/MyApp.app", runner=runner)
    joined = [" ".join(a) for a in calls]
    assert any("mkdir -p ~/Apps" in c for c in joined)
    assert any(c.startswith("scp ") and "MyApp.app" in c and "admin@10.0.0.5:~/Apps/" in c
               for c in joined)


def test_launch_app_opens_absolute_path():
    calls = []
    def runner(args, **kw):
        calls.append(args); return _ok()
    hs.launch_app("pool-1", "MyApp.app", "/bin/tart", runner=runner)
    assert calls[0] == ["/bin/tart", "exec", "pool-1", "open", "/Users/admin/Apps/MyApp.app"]


def test_enable_screen_sharing_runs_kickstart_and_returns_password():
    calls = []
    def runner(args, **kw):
        calls.append(args); return _ok()
    pw = hs.enable_screen_sharing("10.0.0.5", "/k/key", runner=runner, password="secret12")
    assert pw == "secret12"
    joined = " ".join(" ".join(a) for a in calls)
    assert "kickstart" in joined
    assert "-setvncpw -vncpw secret12" in joined
    assert "-activate" in joined and "-restart -agent" in joined


def test_enable_screen_sharing_generates_password_when_absent():
    def runner(args, **kw): return _ok()
    pw = hs.enable_screen_sharing("10.0.0.5", "/k/key", runner=runner)
    assert isinstance(pw, str) and len(pw) == 8


def test_open_screen_sharing_opens_vnc_url():
    calls = []
    def opener(args, **kw):
        calls.append(args); return _ok()
    hs.open_screen_sharing("10.0.0.5", "secret12", opener=opener)
    assert calls[0] == ["open", "vnc://:secret12@10.0.0.5"]


def test_vnc_connection_probe_true_when_established():
    def runner(args, **kw):
        return _ok(stdout="tcp4  0 0 10.0.0.5.5900 10.0.0.1.51000 ESTABLISHED\n", rc=0)
    assert hs.vnc_connection_probe("10.0.0.5", "/k/key", runner=runner) is True


def test_vnc_connection_probe_false_when_no_connection():
    def runner(args, **kw):
        return _ok(stdout="", rc=1)
    assert hs.vnc_connection_probe("10.0.0.5", "/k/key", runner=runner) is False
