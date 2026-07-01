from mac_vm_pool.config import Config
from mac_vm_pool.host import FakeHost
from mac_vm_pool.pool import LeasePool
from mac_vm_pool import server

def make_injected_server():
    cfg = Config.load(None)
    pool = LeasePool(FakeHost(capacity_limit=2), cfg)
    return server.PoolService(pool)

def test_acquire_then_status_then_release():
    svc = make_injected_server()
    res = svc.acquire_vm("agent-1")
    assert res["state"] == "leased"
    lid = res["lease_id"]
    assert svc.vm_status(lid)["lease_id"] == lid
    assert svc.release_vm(lid) == {"released": True}
    assert svc.vm_status(lid) == {"found": False}

def test_acquire_reports_queued_at_cap():
    svc = make_injected_server()
    svc.acquire_vm("a"); svc.acquire_vm("b")
    assert svc.acquire_vm("c") == {"queued": True, "reason": "cap_reached"}

def test_list_pool_reports_free_capacity():
    svc = make_injected_server()
    svc.acquire_vm("a")
    listing = svc.list_pool()
    assert listing["capacity_free"] == 1
    assert len(listing["leases"]) == 1
