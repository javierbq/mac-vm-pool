from unittest.mock import MagicMock
from mac_vm_pool.config import Config
from mac_vm_pool import baker

def test_bake_runs_expected_stages_in_order():
    cfg = Config.load(None)
    calls = []
    def runner(args, **kw):
        calls.append(args)
        m = MagicMock(); m.returncode = 0
        m.stdout = "10.0.0.9" if args[:2] == [cfg.tart_bin, "ip"] else "ok"
        m.stderr = ""
        return m
    result = baker.bake_golden_image(cfg, runner=runner)
    assert result["image"] == cfg.golden_image
    joined = [" ".join(a) for a in calls]
    # base clone happens before the final commit
    assert any(c.startswith(f"{cfg.tart_bin} clone {cfg.base_image}") for c in joined)
    assert any("tccutil" not in c and "TCC.db" in c for c in joined)  # TCC grant issued
    assert joined[-1].startswith(f"{cfg.tart_bin} ")                  # ends on a tart op
