from unittest.mock import MagicMock
from mac_vm_pool.config import Config
from mac_vm_pool import baker


def test_bake_runs_expected_stages_in_order(tmp_path):
    cfg = Config.load(None)

    # Fix 1: create a real temp pubkey file so the missing-key guard doesn't fire
    pubkey_content = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI testkey bake-test"
    pub_file = tmp_path / "id_ed25519.pub"
    pub_file.write_text(pubkey_content)
    cfg.ssh_key_path = str(tmp_path / "id_ed25519")

    calls = []

    def runner(args, **kw):
        calls.append(args)
        m = MagicMock()
        m.returncode = 0
        m.stdout = "10.0.0.9" if args[:2] == [cfg.tart_bin, "ip"] else "ok"
        m.stderr = ""
        return m

    # Fix 2: fake launcher — records call, does NOT spawn a real tart process
    launched = []

    def fake_launcher(args):
        launched.append(args)

    result = baker.bake_golden_image(cfg, runner=runner, launcher=fake_launcher)

    assert result["image"] == cfg.golden_image

    joined = [" ".join(a) for a in calls]

    # base clone happens before the final commit
    assert any(c.startswith(f"{cfg.tart_bin} clone {cfg.base_image}") for c in joined)

    # Fix 2: launcher was called with tart run BUILD_VM (no real process spawned)
    assert len(launched) == 1
    assert launched[0] == [cfg.tart_bin, "run", baker.BUILD_VM]

    # Fix 1: SSH pubkey was baked into authorized_keys
    assert any("authorized_keys" in c for c in joined), "authorized_keys command missing"

    # Fix 5: TCC grant issues INSERT INTO access and covers all three services
    tcc_cmds = [c for c in joined if "INSERT" in c and "INTO access" in c]
    assert tcc_cmds, "No INSERT INTO access TCC command found"
    all_tcc = " ".join(tcc_cmds)
    assert "kTCCServiceAccessibility" in all_tcc
    assert "kTCCServiceScreenCapture" in all_tcc
    assert "kTCCServicePostEvent" in all_tcc

    # ends on a tart op
    assert joined[-1].startswith(f"{cfg.tart_bin} ")
