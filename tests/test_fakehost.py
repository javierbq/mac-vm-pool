import pytest
from mac_vm_pool.host import FakeHost

def test_clone_boot_ip_delete_cycle():
    h = FakeHost(capacity_limit=2)
    assert h.running() == []
    h.clone("golden", "vm1")
    assert h.exists("vm1")
    assert h.running() == []            # cloned but not booted
    h.boot("vm1")
    assert h.running() == ["vm1"]
    ip = h.wait_ip("vm1", timeout=1)
    assert ip.startswith("10.0.0.")
    h.delete("vm1")
    assert not h.exists("vm1")
    assert h.running() == []

def test_capacity_reports_free_slots():
    h = FakeHost(capacity_limit=2)
    h.clone("g", "a"); h.boot("a")
    assert h.capacity() == 1            # 2 - 1 running
    h.clone("g", "b"); h.boot("b")
    assert h.capacity() == 0
