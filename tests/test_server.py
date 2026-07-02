from mac_vm_pool.config import Config
from mac_vm_pool.host import FakeHost
from mac_vm_pool.pool import LeasePool
from mac_vm_pool import server
from mac_vm_pool import human_session

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

def test_start_human_session_orchestrates(monkeypatch):
    svc = make_injected_server()
    lid = svc.acquire_vm("a")["lease_id"]
    ip = svc.vm_status(lid)["ip"]
    vm_name = svc.vm_status(lid)["vm_name"]
    calls = {}
    monkeypatch.setattr(human_session, "deploy_app",
                        lambda *a, **k: calls.__setitem__("deploy", a))
    monkeypatch.setattr(human_session, "launch_app",
                        lambda *a, **k: calls.__setitem__("launch", a))
    monkeypatch.setattr(human_session, "enable_screen_sharing",
                        lambda *a, **k: "pw012345")
    monkeypatch.setattr(human_session, "open_screen_sharing",
                        lambda *a, **k: calls.__setitem__("open", a))

    started = {}

    class FakeMonitor:
        def __init__(self, *a, **k):
            started["kwargs"] = k
        def start(self):
            started["started"] = True

    monkeypatch.setattr(human_session, "HumanSessionMonitor", FakeMonitor)

    out = svc.start_human_session(lid, app_path="/tmp/MyApp.app")
    assert out == {"vnc_url": f"vnc://:pw012345@{ip}",
                   "vm_name": vm_name, "ip": ip, "monitoring": True}
    assert "deploy" in calls and "launch" in calls and "open" in calls
    assert started["started"] is True

def test_start_human_session_skips_deploy_without_app(monkeypatch):
    svc = make_injected_server()
    lid = svc.acquire_vm("a")["lease_id"]
    calls = {}
    monkeypatch.setattr(human_session, "deploy_app",
                        lambda *a, **k: calls.__setitem__("deploy", True))
    monkeypatch.setattr(human_session, "enable_screen_sharing",
                        lambda *a, **k: "pw012345")
    monkeypatch.setattr(human_session, "open_screen_sharing", lambda *a, **k: None)
    monkeypatch.setattr(human_session, "HumanSessionMonitor",
                        lambda *a, **k: type("M", (), {"start": lambda self: None})())
    out = svc.start_human_session(lid)
    assert out["monitoring"] is True
    assert "deploy" not in calls

def test_start_human_session_unknown_lease():
    svc = make_injected_server()
    assert svc.start_human_session("nope") == {"found": False}
