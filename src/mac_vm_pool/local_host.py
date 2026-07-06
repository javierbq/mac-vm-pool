from __future__ import annotations
import subprocess
import time
from mac_vm_pool.config import Config


class LocalHost:
    def __init__(self, config: Config):
        self.cfg = config
        self.tart = config.tart_bin

    def _run(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        return subprocess.run([self.tart, *args], capture_output=True, text=True, check=check)

    def capacity(self) -> int:
        return self.cfg.max_vms_per_host - len(self.running())

    def running(self) -> list[str]:
        out = self._run("list").stdout.splitlines()
        names = []
        for line in out[1:]:                      # skip header
            parts = line.split()
            if len(parts) >= 2 and parts[-1] == "running" and parts[0] == "local":
                names.append(parts[1])
        return names

    def clone(self, src: str, name: str) -> None:
        self._run("clone", src, name)

    def boot(self, name: str, graphics: bool = False) -> None:
        # tart run is long-running; detach it. Headless by default — the pool
        # drives via RPC and human sessions use Screen Sharing, so the built-in
        # window is never needed.
        args = [self.tart, "run", name]
        if not graphics:
            args.append("--no-graphics")
        subprocess.Popen(
            args,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,
        )

    def wait_agent(self, name: str, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._run("exec", name, "true", check=False).returncode == 0:
                return
            time.sleep(5)
        raise TimeoutError(f"guest agent RPC on {name} did not answer within {timeout}s")

    def wait_ip(self, name: str, timeout: float) -> str:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            r = self._run("ip", name, check=False)
            ip = r.stdout.strip()
            if r.returncode == 0 and ip:
                return ip
            time.sleep(3)
        raise TimeoutError(f"VM {name} did not acquire an IP within {timeout}s")

    def delete(self, name: str) -> None:
        self._run("stop", name, check=False)
        self._run("delete", name, check=False)

    def exists(self, name: str) -> bool:
        out = self._run("list").stdout.splitlines()
        return any(name in line.split() for line in out[1:])
