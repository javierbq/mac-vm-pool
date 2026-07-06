from unittest.mock import MagicMock
from mac_vm_pool import human_session as hs


def _ok(stdout="", rc=0):
    m = MagicMock(); m.returncode = rc; m.stdout = stdout; m.stderr = ""
    return m


def test_deploy_app_is_idempotent_and_preserves_symlinks():
    calls = []
    def runner(args, **kw):
        calls.append(args); return _ok()
    hs.deploy_app("10.0.0.5", "/k/key", "/tmp/MyApp.app", runner=runner)
    # clears any prior copy (idempotent) then mkdir, over ssh
    assert any(a[0] == "ssh" and "rm -rf ~/Apps/MyApp.app" in a[-1] and "mkdir -p ~/Apps" in a[-1]
               for a in calls)
    # streams the bundle via tar (preserves symlinks) — NOT scp -r
    sh_calls = [a for a in calls if a[:2] == ["sh", "-c"]]
    assert sh_calls, "expected a tar-over-ssh pipeline"
    script = sh_calls[0][2]
    assert "tar -cf - -C" in script and "tar -xf - -C ~/Apps" in script
    assert "MyApp.app" in script
    assert "scp" not in script


def test_launch_app_opens_absolute_path():
    calls = []
    def runner(args, **kw):
        calls.append(args); return _ok()
    hs.launch_app("pool-1", "MyApp.app", "/bin/tart", runner=runner)
    assert calls[0] == ["/bin/tart", "exec", "pool-1", "open", "/Users/admin/Apps/MyApp.app"]


def test_launch_bundle_opens_by_bundle_id():
    calls = []
    def runner(args, **kw):
        calls.append(args); return _ok()
    hs.launch_bundle("pool-1", "com.example.App", "/bin/tart", runner=runner)
    assert calls[0] == ["/bin/tart", "exec", "pool-1", "open", "-b", "com.example.App"]


def _seq_clock(values):
    it = iter(values)
    return lambda: next(it)


def test_monitor_never_connected():
    m = hs.HumanSessionMonitor(
        probe=lambda: False,
        on_close=lambda: None,
        grace_seconds=30, connect_timeout=10, poll_interval=1,
        clock=_seq_clock([0.0, 0.0, 5.0, 11.0]), sleep=lambda s: None,
    )
    assert m._loop() == "never_connected"


def test_monitor_closes_after_disconnect_grace():
    # sustained connect (2 confirmations) then disconnect held past grace
    probes = iter([True, True, False, False])
    m = hs.HumanSessionMonitor(
        probe=lambda: next(probes),
        on_close=lambda: None,
        grace_seconds=30, connect_timeout=100, poll_interval=1,
        connect_confirmations=2,
        clock=_seq_clock([0.0, 0.0, 100.0]),
        sleep=lambda s: None,
    )
    assert m._loop() == "closed"


def test_monitor_single_blip_does_not_arm_teardown():
    # A single True poll (e.g. a failed-auth handshake) must NOT count as a
    # session — otherwise the VM is reclaimed ~grace seconds after every failed
    # connection attempt (the P3 self-destruct). It should time out instead.
    probes = iter([True, False, False, False])
    m = hs.HumanSessionMonitor(
        probe=lambda: next(probes),
        on_close=lambda: None,
        grace_seconds=1, connect_timeout=5, poll_interval=1,
        connect_confirmations=2,
        clock=_seq_clock([0.0, 0.0, 3.0, 9.0]), sleep=lambda s: None,
    )
    assert m._loop() == "never_connected"


def test_monitor_reconnect_resets_grace_and_keepalives():
    # sustained connect (True,True), then disconnect, reconnect (resets grace +
    # keepalive), disconnect held past grace -> closed
    probes = iter([True, True, False, True, False, False])
    kept = []
    m = hs.HumanSessionMonitor(
        probe=lambda: next(probes),
        on_close=lambda: None,
        grace_seconds=30, connect_timeout=100, poll_interval=1,
        connect_confirmations=2,
        keepalive=lambda: kept.append(True),
        clock=_seq_clock([0.0, 5.0, 50.0, 100.0]),
        sleep=lambda s: None,
    )
    assert m._loop() == "closed"
    assert len(kept) == 1     # keepalive fired on the phase-2 reconnect probe


def test_monitor_run_invokes_on_close():
    closed = []
    m = hs.HumanSessionMonitor(
        probe=lambda: False,
        on_close=lambda: closed.append(True),
        grace_seconds=1, connect_timeout=0, poll_interval=1,
        clock=_seq_clock([0.0, 0.0, 1.0]), sleep=lambda s: None,
    )
    m.run()
    assert m.outcome == "never_connected"
    assert closed == [True]


def test_monitor_stop_exits_and_skips_on_close():
    closed = []
    m = hs.HumanSessionMonitor(
        probe=lambda: True,      # would otherwise stay connected forever
        on_close=lambda: closed.append(True),
        grace_seconds=30, connect_timeout=100, poll_interval=1,
        clock=_seq_clock([0.0, 0.0, 0.0, 0.0]), sleep=lambda s: None,
    )
    m.stop()                     # external release signals the monitor
    m.run()
    assert m.outcome == "stopped"
    assert closed == []          # on_close NOT called when stopped externally
