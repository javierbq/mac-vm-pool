from unittest.mock import patch, MagicMock
from mac_vm_pool.config import Config
from mac_vm_pool.local_host import LocalHost


def _run_ok(stdout=""):
    m = MagicMock(); m.returncode = 0; m.stdout = stdout; m.stderr = ""
    return m


def test_running_parses_tart_list():
    cfg = Config.load(None)
    h = LocalHost(cfg)
    listing = (
        "Source Name          Disk Size Accessed State\n"
        "local  pool-aaa       50   33   now      running\n"
        "local  pool-bbb       50   33   now      stopped\n"
    )
    with patch("subprocess.run", return_value=_run_ok(listing)):
        assert h.running() == ["pool-aaa"]


def test_clone_invokes_tart_clone():
    cfg = Config.load(None)
    h = LocalHost(cfg)
    with patch("subprocess.run", return_value=_run_ok()) as run:
        h.clone("mac-test-golden", "pool-xyz")
        args = run.call_args[0][0]
        assert args[:3] == [cfg.tart_bin, "clone", "mac-test-golden"]
        assert args[3] == "pool-xyz"


from mac_vm_pool.host import Host


def test_localhost_is_a_host():
    assert isinstance(LocalHost(Config.load(None)), Host)
