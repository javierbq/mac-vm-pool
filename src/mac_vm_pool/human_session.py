from __future__ import annotations
import os
import shlex
import subprocess
import threading
import time

SSH_OPTS = ["-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null"]


def _ssh(ip: str, key: str, remote_cmd: str, runner) -> subprocess.CompletedProcess:
    return runner(
        ["ssh", "-i", os.path.expanduser(key), *SSH_OPTS, f"admin@{ip}", remote_cmd],
        capture_output=True, text=True, check=False,
    )


def deploy_app(ip: str, ssh_key: str, app_path: str, runner=subprocess.run) -> None:
    # Stream the bundle with tar rather than `scp -r`: scp -r dereferences the
    # internal symlinks a .app/framework relies on, which corrupts the bundle's
    # code signature (Gatekeeper "damaged" on launch); tar preserves them. Clear
    # any prior copy first so a re-deploy onto the same lease is idempotent
    # (scp -r into an existing dir nests the bundle instead of replacing it).
    key = os.path.expanduser(ssh_key)
    base = os.path.basename(app_path.rstrip("/"))
    parent = os.path.dirname(os.path.abspath(app_path.rstrip("/")))
    _ssh(ip, key, f"rm -rf ~/Apps/{shlex.quote(base)} && mkdir -p ~/Apps", runner)
    ssh_cmd = " ".join(["ssh", "-i", shlex.quote(key), *SSH_OPTS,
                        f"admin@{ip}", "tar -xf - -C ~/Apps"])
    pipeline = (f"set -o pipefail; tar -cf - -C {shlex.quote(parent)} "
                f"{shlex.quote(base)} | {ssh_cmd}")
    runner(["sh", "-c", pipeline], capture_output=True, text=True, check=True)


def launch_app(vm_name: str, app_basename: str, tart_bin: str, runner=subprocess.run) -> None:
    runner(
        [tart_bin, "exec", vm_name, "open", f"/Users/admin/Apps/{app_basename}"],
        capture_output=True, text=True, check=True,
    )


def launch_bundle(vm_name: str, bundle_id: str, tart_bin: str, runner=subprocess.run) -> None:
    runner(
        [tart_bin, "exec", vm_name, "open", "-b", bundle_id],
        capture_output=True, text=True, check=True,
    )


def open_screen_sharing(ip: str, user: str, password: str, opener=subprocess.run) -> None:
    # Account auth (macOS Screen Sharing's default). Screen Sharing is enabled
    # with account access at bake time, so there is NO per-session guest
    # reconfiguration here — that (kickstart -restart -agent) was destabilizing
    # live VMs, and legacy VNC-password auth was rejected by the host.
    opener(["open", f"vnc://{user}:{password}@{ip}"], check=False)


def vnc_connection_probe(ip: str, ssh_key: str, vnc_port: int = 5900,
                         runner=subprocess.run) -> bool:
    cmd = f"netstat -an -p tcp | grep '\\.{vnc_port} ' | grep ESTABLISHED"
    r = _ssh(ip, os.path.expanduser(ssh_key), cmd, runner)
    return r.returncode == 0 and bool(r.stdout.strip())


class HumanSessionMonitor(threading.Thread):
    """Backstop teardown for a manual session. Explicit release_vm is the primary
    path; this thread only auto-releases when a SUSTAINED, real Screen Sharing
    session ends (disconnect held for grace_seconds), or if nobody ever connects
    (connect_timeout). It refreshes the lease via keepalive() while connected.

    A connection must be observed on `connect_confirmations` consecutive polls
    before it counts as "connected" — a single-poll blip (e.g. an auth handshake
    that immediately fails, or a transient TCP probe) must not arm teardown, which
    was reclaiming VMs ~grace seconds after every failed connection attempt."""

    def __init__(self, probe, on_close, *, grace_seconds, connect_timeout,
                 poll_interval=3.0, keepalive=None, connect_confirmations=2,
                 clock=time.monotonic, sleep=time.sleep):
        super().__init__(daemon=True)
        self._probe = probe
        self._on_close = on_close
        self._keepalive = keepalive or (lambda: None)
        self._grace = grace_seconds
        self._connect_timeout = connect_timeout
        self._confirm = max(1, connect_confirmations)
        self._poll = poll_interval
        self._clock = clock
        self._sleep = sleep
        self._stop = threading.Event()
        self.outcome = None

    def stop(self):
        """Signal the monitor to exit its poll loop (e.g. on explicit release),
        so it stops SSHing into a VM that is being torn down elsewhere."""
        self._stop.set()

    def run(self):
        self.outcome = self._loop()
        # Only auto-release when WE detected the end of the session. If we were
        # stopped externally (release_vm already ran), don't release again.
        if self.outcome in ("closed", "never_connected"):
            self._on_close()

    def _loop(self):
        # Phase 1: wait for a SUSTAINED first connection (>= _confirm polls in a
        # row). A momentary blip resets the streak so it can't arm teardown.
        start = self._clock()
        streak = 0
        while not self._stop.is_set():
            if self._probe():
                streak += 1
                if streak >= self._confirm:
                    break
            else:
                streak = 0
                if self._clock() - start >= self._connect_timeout:
                    return "never_connected"
            self._sleep(self._poll)
        if self._stop.is_set():
            return "stopped"
        # Phase 2: wait for a disconnect held past the grace window.
        disconnected_since = None
        while not self._stop.is_set():
            if self._probe():
                self._keepalive()
                disconnected_since = None
            elif disconnected_since is None:
                disconnected_since = self._clock()
            elif self._clock() - disconnected_since >= self._grace:
                return "closed"
            self._sleep(self._poll)
        return "stopped"
