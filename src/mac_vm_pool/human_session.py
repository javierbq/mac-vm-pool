from __future__ import annotations
import os
import secrets
import subprocess

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
