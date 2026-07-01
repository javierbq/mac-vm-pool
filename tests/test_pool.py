import itertools
from mac_vm_pool.config import Config
from mac_vm_pool.host import FakeHost
from mac_vm_pool.pool import LeasePool

def make_pool(clock_values=None):
    cfg = Config.load(None)
    host = FakeHost(capacity_limit=cfg.max_vms_per_host)
    counter = itertools.count(1)
    namegen = lambda: f"pool-{next(counter)}"
    if clock_values is not None:
        it = iter(clock_values)
        clock = lambda: next(it)
    else:
        clock = lambda: 1000.0
    return LeasePool(host, cfg, clock=clock, namegen=namegen), host

def test_acquire_returns_leased_vm_with_ip():
    pool, host = make_pool()
    lease = pool.acquire("agent-1")
    assert lease is not None
    assert lease.state == "leased"
    assert lease.ip.startswith("10.0.0.")
    assert lease.vm_name in host.running()

def test_cap_blocks_third_acquire():
    pool, _ = make_pool()
    assert pool.acquire("a") is not None
    assert pool.acquire("b") is not None
    assert pool.acquire("c") is None          # cap = 2

def test_release_frees_a_slot():
    pool, host = make_pool()
    a = pool.acquire("a"); pool.acquire("b")
    assert pool.acquire("c") is None
    assert pool.release(a.lease_id) is True
    assert a.vm_name not in host.running()
    assert pool.acquire("c") is not None       # slot freed

def test_reap_expired_lease():
    # clock: acquire sees 1000 (created), 1000 (expires calc); reap sees 9999
    pool, host = make_pool(clock_values=[1000.0, 1000.0, 9999.0, 9999.0])
    lease = pool.acquire("a", ttl=10)
    vm = lease.vm_name
    reaped = pool.reap()
    assert lease.lease_id in reaped
    assert vm not in host.running()

def test_acquire_honors_zero_ttl():
    # clock returns constant 1000.0; ttl=0 means expires_at == created_at
    pool, _ = make_pool()
    lease = pool.acquire("a", ttl=0)
    assert lease is not None
    assert lease.expires_at == lease.created_at

def test_reconcile_destroys_orphans_only():
    pool, host = make_pool()
    lease = pool.acquire("a")               # tracked pool VM
    host.clone("g", "pool-orphan"); host.boot("pool-orphan")  # crash orphan
    host.clone("g", "user-vm"); host.boot("user-vm")          # unrelated VM
    deleted = pool.reconcile()
    assert "pool-orphan" in deleted
    assert lease.vm_name not in deleted     # tracked lease survives
    assert "user-vm" not in deleted         # non-pool VM untouched
    assert "user-vm" in host.running()
