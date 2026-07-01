from __future__ import annotations
from dataclasses import dataclass, asdict

@dataclass
class Lease:
    lease_id: str
    vm_name: str
    client_id: str
    state: str          # warming | ready | leased | releasing | dead
    ip: str | None
    created_at: float
    expires_at: float

    def to_dict(self) -> dict:
        return asdict(self)
