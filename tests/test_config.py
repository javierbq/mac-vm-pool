import os
from mac_vm_pool.config import Config

def test_defaults_match_spec():
    cfg = Config.load(None)
    assert cfg.max_vms_per_host == 2
    assert cfg.warm_target == 1
    assert cfg.golden_image == "mac-test-golden"
    assert cfg.base_image == "ghcr.io/cirruslabs/macos-sequoia-base:latest"
    assert cfg.lease_ttl == 1800

def test_env_override(monkeypatch):
    monkeypatch.setenv("MVP_MAX_VMS_PER_HOST", "1")
    cfg = Config.load(None)
    assert cfg.max_vms_per_host == 1
