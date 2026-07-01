from __future__ import annotations
import os
import tomllib
from dataclasses import dataclass, fields
from typing import get_type_hints

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
        data: dict[str, object] = {}
        if path:
            expanded = os.path.expanduser(path)
            if os.path.exists(expanded):
                with open(expanded, "rb") as fh:
                    data = tomllib.load(fh)
        hints = get_type_hints(cls)
        kwargs = {}
        for f in fields(cls):
            env = os.environ.get(f"MVP_{f.name.upper()}")
            raw = env if env is not None else data.get(f.name)
            if raw is None:
                continue
            kwargs[f.name] = int(raw) if hints[f.name] is int and not isinstance(raw, int) else raw
        return cls(**kwargs)
