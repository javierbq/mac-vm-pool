from __future__ import annotations
from mac_vm_pool.config import Config
from mac_vm_pool.local_host import LocalHost
from mac_vm_pool.pool import LeasePool
from mac_vm_pool.baker import bake_golden_image

class PoolService:
    """Transport-agnostic tool implementations, unit-testable with a FakeHost."""
    def __init__(self, pool: LeasePool, cfg: Config | None = None):
        self.pool = pool
        self.cfg = cfg or Config.load(None)

    def acquire_vm(self, client_id: str, ttl_seconds: int = 1800) -> dict:
        lease = self.pool.acquire(client_id, ttl=ttl_seconds)
        if lease is None:
            return {"queued": True, "reason": "cap_reached"}
        return lease.to_dict()

    def release_vm(self, lease_id: str) -> dict:
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
    mcp.run(transport="streamable-http")

if __name__ == "__main__":
    main()
