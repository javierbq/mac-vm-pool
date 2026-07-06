import os
from mac_vm_pool.config import Config

def test_defaults_match_spec():
    cfg = Config.load(None)
    assert cfg.max_vms_per_host == 2
    assert cfg.warm_target == 1
    assert cfg.golden_image == "mac-test-golden"
    assert cfg.base_image == "ghcr.io/cirruslabs/macos-sequoia-base:latest"
    assert cfg.lease_ttl == 1800

def test_human_session_defaults():
    cfg = Config.load(None)
    assert cfg.human_session_grace_seconds == 30
    assert cfg.human_session_connect_timeout == 600
    assert cfg.human_session_connect_confirmations == 2
    assert cfg.human_session_ttl == 14400
    assert cfg.vnc_port == 5900
    assert cfg.vnc_user == "admin"
    assert cfg.vnc_password == "admin"

def test_env_override(monkeypatch):
    monkeypatch.setenv("MVP_MAX_VMS_PER_HOST", "1")
    cfg = Config.load(None)
    assert cfg.max_vms_per_host == 1

def test_env_override_zero(monkeypatch):
    monkeypatch.setenv("MVP_MAX_VMS_PER_HOST", "0")
    cfg = Config.load(None)
    assert cfg.max_vms_per_host == 0

def test_toml_load(tmp_path):
    toml_file = tmp_path / "config.toml"
    toml_file.write_text('max_vms_per_host = 1\ngolden_image = "custom"\n')
    cfg = Config.load(str(toml_file))
    assert cfg.max_vms_per_host == 1
    assert cfg.golden_image == "custom"
