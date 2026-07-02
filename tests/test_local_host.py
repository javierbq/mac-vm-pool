import pytest
from unittest.mock import patch, MagicMock
from mac_vm_pool.config import Config
from mac_vm_pool.local_host import LocalHost


def _run_ok(stdout=""):
    m = MagicMock(); m.returncode = 0; m.stdout = stdout; m.stderr = ""
    return m


def _run_fail(stderr=""):
    m = MagicMock(); m.returncode = 1; m.stdout = ""; m.stderr = stderr
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


def test_wait_agent_returns_when_rpc_answers():
    cfg = Config.load(None)
    h = LocalHost(cfg)
    with patch("subprocess.run", return_value=_run_ok()):
        assert h.wait_agent("pool-x", timeout=30) is None


def test_wait_agent_raises_on_timeout():
    cfg = Config.load(None)
    h = LocalHost(cfg)
    with patch("subprocess.run", return_value=_run_fail()), \
         patch("mac_vm_pool.local_host.time.sleep"), \
         patch("mac_vm_pool.local_host.time.monotonic", side_effect=[0.0, 0.0, 100.0]):
        with pytest.raises(TimeoutError):
            h.wait_agent("pool-x", timeout=30)


def test_boot_is_headless_by_default():
    cfg = Config.load(None)
    h = LocalHost(cfg)
    with patch("subprocess.Popen") as popen:
        h.boot("pool-x")
        assert popen.call_args[0][0] == [cfg.tart_bin, "run", "pool-x", "--no-graphics"]


def test_boot_with_graphics_omits_flag():
    cfg = Config.load(None)
    h = LocalHost(cfg)
    with patch("subprocess.Popen") as popen:
        h.boot("pool-x", graphics=True)
        assert popen.call_args[0][0] == [cfg.tart_bin, "run", "pool-x"]


from mac_vm_pool.host import Host


def test_localhost_is_a_host():
    assert isinstance(LocalHost(Config.load(None)), Host)
