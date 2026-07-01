from __future__ import annotations
import time
import uuid
import threading
from mac_vm_pool.config import Config
from mac_vm_pool.host import Host
from mac_vm_pool.lease import Lease

class LeasePool:
    def __init__(self, host: Host, config: Config, clock=time.monotonic, namegen=None):
        self.host = host
        self.cfg = config
        self.clock = clock
        self._namegen = namegen or (lambda: f"pool-{uuid.uuid4().hex[:8]}")
        self._leases: dict[str, Lease] = {}
        self._lock = threading.RLock()

    def acquire(self, client_id: str, ttl: int | None = None) -> Lease | None:
        with self._lock:
            if self.host.capacity() <= 0:
                return None
            name = self._namegen()
            now = self.clock()
            ttl = ttl or self.cfg.lease_ttl
            expires_at = self.clock() + ttl
            self.host.clone(self.cfg.golden_image, name)
            self.host.boot(name)
            ip = self.host.wait_ip(name, timeout=self.cfg.acquire_wait_timeout)
            lease = Lease(
                lease_id=uuid.uuid4().hex,
                vm_name=name,
                client_id=client_id,
                state="leased",
                ip=ip,
                created_at=now,
                expires_at=expires_at,
            )
            self._leases[lease.lease_id] = lease
            return lease

    def release(self, lease_id: str) -> bool:
        with self._lock:
            lease = self._leases.pop(lease_id, None)
            if lease is None:
                return False
            lease.state = "releasing"
            self.host.delete(lease.vm_name)
            return True

    def status(self, lease_id: str) -> Lease | None:
        with self._lock:
            return self._leases.get(lease_id)

    def leases(self) -> list[Lease]:
        with self._lock:
            return list(self._leases.values())

    def reap(self, now: float | None = None) -> list[str]:
        with self._lock:
            now = now if now is not None else self.clock()
            expired = [l.lease_id for l in self._leases.values() if l.expires_at <= now]
            for lid in expired:
                lease = self._leases.pop(lid)
                self.host.delete(lease.vm_name)
            return expired

    def reconcile(self) -> list[str]:
        with self._lock:
            live = {l.vm_name for l in self._leases.values()}
            deleted = []
            for name in self.host.running():
                if name.startswith("pool-") and name not in live:
                    self.host.delete(name)
                    deleted.append(name)
            return deleted
