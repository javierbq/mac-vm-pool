from __future__ import annotations
from typing import Protocol, runtime_checkable

@runtime_checkable
class Host(Protocol):
    def capacity(self) -> int: ...
    def running(self) -> list[str]: ...
    def clone(self, src: str, name: str) -> None: ...
    def boot(self, name: str) -> None: ...
    def wait_ip(self, name: str, timeout: float) -> str: ...
    def delete(self, name: str) -> None: ...
    def exists(self, name: str) -> bool: ...

class FakeHost:
    """In-memory Host for unit tests. Never touches tart."""
    def __init__(self, capacity_limit: int = 2):
        self.capacity_limit = capacity_limit
        self._vms: dict[str, dict] = {}   # name -> {booted: bool, ip: str|None}
        self._ip_seq = 0

    def capacity(self) -> int:
        return self.capacity_limit - len(self.running())

    def running(self) -> list[str]:
        return [n for n, v in self._vms.items() if v["booted"]]

    def clone(self, src: str, name: str) -> None:
        self._vms[name] = {"booted": False, "ip": None}

    def boot(self, name: str) -> None:
        self._vms[name]["booted"] = True

    def wait_ip(self, name: str, timeout: float) -> str:
        self._ip_seq += 1
        ip = f"10.0.0.{self._ip_seq}"
        self._vms[name]["ip"] = ip
        return ip

    def delete(self, name: str) -> None:
        self._vms.pop(name, None)

    def exists(self, name: str) -> bool:
        return name in self._vms
