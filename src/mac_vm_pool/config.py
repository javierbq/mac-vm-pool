from __future__ import annotations
import os
import tomllib
from dataclasses import dataclass, fields

@dataclass
class Config:
    ssh_key_path: str = "~/.mac-vm-pool/id_ed25519"
    guest_agent_binary: str = "~/repos/tart-guest-agent/tart-guest-agent"
    tart_bin: str = "tart"
    max_vms_per_host: int = 2
    warm_target: int = 1
    golden_image: str = "mac-test-golden"
    base_image: str = "ghcr.io/cirruslabs/macos-sequoia-base:latest"
    lease_ttl: int = 1800
    acquire_wait_timeout: int = 300

    @classmethod
    def load(cls, path: str | None) -> "Config":
        data: dict = {}
        if path and os.path.exists(os.path.expanduser(path)):
            with open(os.path.expanduser(path), "rb") as fh:
                data = tomllib.load(fh)
        kwargs = {}
        for f in fields(cls):
            env = os.environ.get(f"MVP_{f.name.upper()}")
            if env is not None:
                kwargs[f.name] = f.type == "int" and int(env) or (int(env) if f.type is int else env)
            elif f.name in data:
                kwargs[f.name] = data[f.name]
        return cls(**kwargs)
