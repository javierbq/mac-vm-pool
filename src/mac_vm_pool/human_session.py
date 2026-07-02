from __future__ import annotations
import os
import secrets
import subprocess
import threading
import time

SSH_OPTS = ["-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null"]
KICKSTART = ("/System/Library/CoreServices/RemoteManagement/"
             "ARDAgent.app/Contents/Resources/kickstart")


def _ssh(ip: str, key: str, remote_cmd: str, runner) -> subprocess.CompletedProcess:
    return runner(
        ["ssh", "-i", os.path.expanduser(key), *SSH_OPTS, f"admin@{ip}", remote_cmd],
        capture_output=True, text=True, check=False,
    )


def deploy_app(ip: str, ssh_key: str, app_path: str, runner=subprocess.run) -> None:
    key = os.path.expanduser(ssh_key)
    _ssh(ip, key, "mkdir -p ~/Apps", runner)
    runner(
        ["scp", "-i", key, *SSH_OPTS, "-r", app_path, f"admin@{ip}:~/Apps/"],
        capture_output=True, text=True, check=True,
    )


def launch_app(vm_name: str, app_basename: str, tart_bin: str, runner=subprocess.run) -> None:
    runner(
        [tart_bin, "exec", vm_name, "open", f"/Users/admin/Apps/{app_basename}"],
        capture_output=True, text=True, check=True,
    )


def enable_screen_sharing(ip: str, ssh_key: str, runner=subprocess.run,
                          password: str | None = None) -> str:
    password = password or secrets.token_hex(4)   # 8 hex chars (VNC legacy pw max)
    cmd = (
        f"echo admin | sudo -S {KICKSTART} -activate -configure -access -on "
        f"-restart -agent -privs -all -clientopts -setvnclegacy -vnclegacy yes "
        f"-setvncpw -vncpw {password}"
    )
    _ssh(ip, os.path.expanduser(ssh_key), cmd, runner)
    return password


def open_screen_sharing(ip: str, password: str, opener=subprocess.run) -> None:
    opener(["open", f"vnc://:{password}@{ip}"], check=False)


def vnc_connection_probe(ip: str, ssh_key: str, vnc_port: int = 5900,
                         runner=subprocess.run) -> bool:
    cmd = f"netstat -an -p tcp | grep '\\.{vnc_port} ' | grep ESTABLISHED"
    r = _ssh(ip, os.path.expanduser(ssh_key), cmd, runner)
    return r.returncode == 0 and bool(r.stdout.strip())


class HumanSessionMonitor(threading.Thread):
    """Watch a guest's VNC connection; call on_close() when the human's Screen
    Sharing session ends (disconnect held for grace_seconds) or never starts
    (connect_timeout). Refresh the lease via keepalive() while connected."""

    def __init__(self, probe, on_close, *, grace_seconds, connect_timeout,
                 poll_interval=3.0, keepalive=None,
                 clock=time.monotonic, sleep=time.sleep):
        super().__init__(daemon=True)
        self._probe = probe
        self._on_close = on_close
        self._keepalive = keepalive or (lambda: None)
        self._grace = grace_seconds
        self._connect_timeout = connect_timeout
        self._poll = poll_interval
        self._clock = clock
        self._sleep = sleep
        self.outcome = None

    def run(self):
        self.outcome = self._loop()
        self._on_close()

    def _loop(self):
        # Phase 1: wait for the first connection.
        start = self._clock()
        while not self._probe():
            if self._clock() - start >= self._connect_timeout:
                return "never_connected"
            self._sleep(self._poll)
        # Phase 2: wait for a disconnect held past the grace window.
        disconnected_since = None
        while True:
            if self._probe():
                self._keepalive()
                disconnected_since = None
            elif disconnected_since is None:
                disconnected_since = self._clock()
            elif self._clock() - disconnected_since >= self._grace:
                return "closed"
            self._sleep(self._poll)
