from __future__ import annotations
import os
import threading
from mac_vm_pool.config import Config
from mac_vm_pool.local_host import LocalHost
from mac_vm_pool.pool import LeasePool
from mac_vm_pool.baker import bake_golden_image
from mac_vm_pool import human_session

class PoolService:
    """Transport-agnostic tool implementations, unit-testable with a FakeHost."""
    def __init__(self, pool: LeasePool, cfg: Config | None = None):
        self.pool = pool
        self.cfg = cfg or Config.load(None)
        self._monitors: dict = {}
        self._monitors_lock = threading.Lock()

    def acquire_vm(self, client_id: str, ttl_seconds: int = 1800) -> dict:
        lease = self.pool.acquire(client_id, ttl=ttl_seconds)
        if lease is None:
            return {"queued": True, "reason": "cap_reached"}
        return lease.to_dict()

    def release_vm(self, lease_id: str) -> dict:
        with self._monitors_lock:
            monitor = self._monitors.pop(lease_id, None)
        if monitor is not None:
            monitor.stop()
        return {"released": self.pool.release(lease_id)}

    def vm_status(self, lease_id: str) -> dict:
        lease = self.pool.status(lease_id)
        return lease.to_dict() if lease else {"found": False}

    def list_pool(self) -> dict:
        return {
            "leases": [l.to_dict() for l in self.pool.leases()],
            "capacity_free": self.pool.host.capacity(),
        }

    def provision_golden_image(self) -> dict:
        return bake_golden_image(self.cfg)

    def start_human_session(self, lease_id: str, app_path: str | None = None,
                            bundle_id: str | None = None) -> dict:
        """Prepare a leased VM for hands-on human testing: install+launch the
        app (if given), enable Screen Sharing in the guest, open a Screen
        Sharing window on the host, and start a monitor that releases the VM
        when the human's session closes."""
        lease = self.pool.status(lease_id)
        if lease is None:
            return {"found": False}
        key = self.cfg.ssh_key_path
        try:
            if app_path:
                human_session.deploy_app(lease.ip, key, app_path)
                human_session.launch_app(lease.vm_name,
                                         os.path.basename(app_path.rstrip("/")),
                                         self.cfg.tart_bin)
            elif bundle_id:
                human_session.launch_bundle(lease.vm_name, bundle_id, self.cfg.tart_bin)
            password = human_session.enable_screen_sharing(lease.ip, key)
            human_session.open_screen_sharing(lease.ip, password)
        except Exception:
            # Setup failed. No reaper is wired, so a lease left behind here would
            # leak a scarce VM slot with no monitor to release it — free it now
            # and let the error surface so the caller can re-acquire and retry.
            self.release_vm(lease_id)
            raise
        self.pool.extend(lease_id, self.cfg.human_session_ttl)
        monitor = human_session.HumanSessionMonitor(
            probe=lambda: human_session.vnc_connection_probe(
                lease.ip, key, self.cfg.vnc_port),
            on_close=lambda: self.release_vm(lease_id),
            grace_seconds=self.cfg.human_session_grace_seconds,
            connect_timeout=self.cfg.human_session_connect_timeout,
            keepalive=lambda: self.pool.extend(lease_id, self.cfg.human_session_ttl),
        )
        with self._monitors_lock:
            self._monitors[lease_id] = monitor
        monitor.start()
        return {"vnc_url": f"vnc://:{password}@{lease.ip}",
                "vm_name": lease.vm_name, "ip": lease.ip, "monitoring": True}

def build_pool(cfg: Config) -> LeasePool:
    pool = LeasePool(LocalHost(cfg), cfg)
    pool.reconcile()
    return pool

def main() -> None:
    from mcp.server.fastmcp import FastMCP
    cfg = Config.load(None)
    svc = PoolService(build_pool(cfg), cfg)
    mcp = FastMCP("mac-vm-pool")
    mcp.tool()(svc.acquire_vm)
    mcp.tool()(svc.release_vm)
    mcp.tool()(svc.vm_status)
    mcp.tool()(svc.list_pool)
    mcp.tool()(svc.provision_golden_image)
    mcp.tool()(svc.start_human_session)
    mcp.run(transport="streamable-http")

if __name__ == "__main__":
    main()
