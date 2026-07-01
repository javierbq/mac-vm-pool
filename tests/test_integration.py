# tests/test_integration.py
import os
import pytest
from mac_vm_pool.config import Config
from mac_vm_pool.server import build_pool, PoolService

pytestmark = pytest.mark.integration

@pytest.mark.skipif(os.environ.get("MVP_RUN_INTEGRATION") != "1",
                    reason="set MVP_RUN_INTEGRATION=1 on a Mac with the golden image")
def test_acquire_release_real_vm():
    cfg = Config.load(None)
    svc = PoolService(build_pool(cfg), cfg)
    lease = svc.acquire_vm("integration")
    assert lease.get("state") == "leased"
    assert lease["ip"]
    try:
        # the leased VM is reachable
        assert svc.vm_status(lease["lease_id"])["state"] == "leased"
    finally:
        assert svc.release_vm(lease["lease_id"]) == {"released": True}
    assert svc.vm_status(lease["lease_id"]) == {"found": False}
