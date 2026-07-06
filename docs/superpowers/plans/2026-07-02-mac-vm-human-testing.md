# mac-vm Human Testing + Pool-Service Consolidation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Route all VM lifecycle through the `mac-vm-pool` service (retiring the skill's bash acquire/release) and add a human-driven manual-testing mode: build/install/launch an app into a fresh VM, hand off via macOS Screen Sharing, and auto-tear-down when the human disconnects.

**Architecture:** Extend the existing `Host`/`LeasePool`/`PoolService` stack. Phase 1 closes the guest-agent RPC-readiness gap in `acquire`, boots headless, registers/runs the MCP server, and rewrites the skill to call MCP tools. Phase 2 adds a `human_session` module (SSH app deploy, Screen Sharing enable via `kickstart`, VNC open, a disconnect monitor) and a `start_human_session` MCP tool.

**Tech Stack:** Python ≥3.12, `mcp>=1.2.0` (FastMCP, streamable-http), stdlib only for new code (`subprocess`, `threading`, `secrets`, `os`, `time`), `pytest` for tests, shells out to `tart` + `ssh`/`scp`/`open`.

## Global Constraints

- Python `requires-python = ">=3.12"`; no new runtime dependencies beyond `mcp>=1.2.0` (new code uses stdlib only).
- All new logic must be unit-testable without a Mac: inject `runner` (subprocess), `clock`, `sleep`, and `probe` callables the way `baker.py`/`pool.py` already do; `FakeHost` stays the test seam.
- MCP transport stays **streamable-http** (parallel agents share one daemon to enforce the 2-VM cap).
- Guest credentials/config are inherent to the cirruslabs base image: user `admin`, password `admin`, SIP off, admin home `/Users/admin`, baked SSH key at `~/.mac-vm-pool/id_ed25519`.
- Run unit tests with `.venv/bin/python -m pytest -q -m "not integration"`. Integration tests are gated behind `MVP_RUN_INTEGRATION=1` and need a real Mac.
- TDD: write the failing test, watch it fail, implement minimally, watch it pass, commit. One logical change per commit.
- Work on branch `mac-vm-human-testing` (already created).

---

## Phase 1 — Parity: pool service becomes the single VM-lifecycle authority

### Task 1: `wait_agent` on the Host seam (protocol + FakeHost)

**Files:**
- Modify: `src/mac_vm_pool/host.py`
- Test: `tests/test_fakehost.py`

**Interfaces:**
- Produces: `Host.wait_agent(name: str, timeout: float) -> None` (protocol method); `FakeHost.wait_agent(name, timeout) -> None` (no-op); `FakeHost.boot(name, graphics: bool = False)` (accepts new kwarg).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_fakehost.py`:

```python
from mac_vm_pool.host import FakeHost


def test_fakehost_wait_agent_is_noop():
    h = FakeHost()
    h.clone("g", "pool-x")
    h.boot("pool-x")
    assert h.wait_agent("pool-x", timeout=1) is None


def test_fakehost_boot_accepts_graphics_kwarg():
    h = FakeHost()
    h.clone("g", "pool-x")
    h.boot("pool-x", graphics=True)
    assert "pool-x" in h.running()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_fakehost.py -q`
Expected: FAIL — `AttributeError: 'FakeHost' object has no attribute 'wait_agent'` and a `TypeError` on the `graphics=` kwarg.

- [ ] **Step 3: Implement**

In `src/mac_vm_pool/host.py`, add to the `Host` Protocol (after `boot`):

```python
    def boot(self, name: str, graphics: bool = False) -> None: ...
    def wait_agent(self, name: str, timeout: float) -> None: ...
```

(Replace the existing `def boot(self, name: str) -> None: ...` line with the `graphics` version above.)

In `FakeHost`, change `boot` and add `wait_agent`:

```python
    def boot(self, name: str, graphics: bool = False) -> None:
        self._vms[name]["booted"] = True

    def wait_agent(self, name: str, timeout: float) -> None:
        return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_fakehost.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/mac_vm_pool/host.py tests/test_fakehost.py
git commit -m "feat(host): add wait_agent seam and graphics boot kwarg"
```

---

### Task 2: `wait_agent` + headless `boot` on LocalHost

**Files:**
- Modify: `src/mac_vm_pool/local_host.py`
- Test: `tests/test_local_host.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `LocalHost.wait_agent(name, timeout)` (polls `tart exec <name> true`, raises `TimeoutError`); `LocalHost.boot(name, graphics=False)` (appends `--no-graphics` when `graphics` is falsy).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_local_host.py` (a `_run_fail` helper and four tests):

```python
import pytest


def _run_fail(stderr=""):
    m = MagicMock(); m.returncode = 1; m.stdout = ""; m.stderr = stderr
    return m


def test_wait_agent_returns_when_rpc_answers():
    cfg = Config.load(None)
    h = LocalHost(cfg)
    with patch("subprocess.run", return_value=_run_ok()):
        assert h.wait_agent("pool-x", timeout=30) is None


def test_wait_agent_raises_on_timeout():
    cfg = Config.load(None)
    h = LocalHost(cfg)
    with patch("subprocess.run", return_value=_run_fail()), \
         patch("mac_vm_pool.local_host.time.sleep"), \
         patch("mac_vm_pool.local_host.time.monotonic", side_effect=[0.0, 0.0, 100.0]):
        with pytest.raises(TimeoutError):
            h.wait_agent("pool-x", timeout=30)


def test_boot_is_headless_by_default():
    cfg = Config.load(None)
    h = LocalHost(cfg)
    with patch("subprocess.Popen") as popen:
        h.boot("pool-x")
        assert popen.call_args[0][0] == [cfg.tart_bin, "run", "pool-x", "--no-graphics"]


def test_boot_with_graphics_omits_flag():
    cfg = Config.load(None)
    h = LocalHost(cfg)
    with patch("subprocess.Popen") as popen:
        h.boot("pool-x", graphics=True)
        assert popen.call_args[0][0] == [cfg.tart_bin, "run", "pool-x"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_local_host.py -q`
Expected: FAIL — `wait_agent` missing; `boot` does not add `--no-graphics`.

- [ ] **Step 3: Implement**

In `src/mac_vm_pool/local_host.py`, replace `boot` and add `wait_agent` after `wait_ip`:

```python
    def boot(self, name: str, graphics: bool = False) -> None:
        # tart run is long-running; detach it. Headless by default — the pool
        # drives via RPC and human sessions use Screen Sharing, so the built-in
        # window is never needed.
        args = [self.tart, "run", name]
        if not graphics:
            args.append("--no-graphics")
        subprocess.Popen(
            args,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,
        )

    def wait_agent(self, name: str, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._run("exec", name, "true", check=False).returncode == 0:
                return
            time.sleep(5)
        raise TimeoutError(f"guest agent RPC on {name} did not answer within {timeout}s")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_local_host.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/mac_vm_pool/local_host.py tests/test_local_host.py
git commit -m "feat(local_host): wait_agent RPC readiness + headless boot"
```

---

### Task 3: `acquire` waits for guest-agent RPC readiness

**Files:**
- Modify: `src/mac_vm_pool/pool.py`
- Test: `tests/test_pool.py`

**Interfaces:**
- Consumes: `host.wait_agent` (Task 1/2).
- Produces: `LeasePool.acquire` now calls `wait_agent(name, timeout=cfg.acquire_wait_timeout)` after `wait_ip` and before building the lease.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_pool.py`:

```python
def test_acquire_waits_for_agent_after_ip():
    cfg = Config.load(None)
    order = []

    class SpyHost(FakeHost):
        def wait_ip(self, name, timeout):
            order.append("wait_ip")
            return super().wait_ip(name, timeout)

        def wait_agent(self, name, timeout):
            order.append("wait_agent")

    pool = LeasePool(SpyHost(capacity_limit=2), cfg, namegen=lambda: "pool-1")
    lease = pool.acquire("a")
    assert lease is not None
    assert order == ["wait_ip", "wait_agent"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_pool.py::test_acquire_waits_for_agent_after_ip -q`
Expected: FAIL — `order == ["wait_ip"]` (wait_agent never called).

- [ ] **Step 3: Implement**

In `src/mac_vm_pool/pool.py`, inside `acquire`, after the `ip = self.host.wait_ip(...)` line add:

```python
            self.host.wait_agent(name, timeout=self.cfg.acquire_wait_timeout)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_pool.py -q`
Expected: PASS (all pool tests, including the new one).

- [ ] **Step 5: Commit**

```bash
git add src/mac_vm_pool/pool.py tests/test_pool.py
git commit -m "fix(pool): acquire waits for guest-agent RPC readiness"
```

---

### Task 4: Register + run the MCP server (config + docs)

**Files:**
- Create: `deploy/com.accipiter.mac-vm-pool.plist` (LaunchAgent template)
- Modify: `README.md`

**Interfaces:** none (ops/docs). Manual verification only.

- [ ] **Step 1: Confirm the server's streamable-http URL**

Run (foreground, then Ctrl-C):
```bash
cd /Users/jcastellanos/repos/mac-vm-pool
MVP_TART_BIN=~/repos/tart/.build/debug/tart .venv/bin/python -m mac_vm_pool.server
```
Expected: FastMCP logs the bind address. Note the URL — FastMCP's streamable-http default is `http://127.0.0.1:8000/mcp`. Record the actual URL from the log for the next steps.

- [ ] **Step 2: Create the LaunchAgent template**

Create `deploy/com.accipiter.mac-vm-pool.plist` (user edits paths as needed):

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.accipiter.mac-vm-pool</string>
  <key>ProgramArguments</key>
  <array>
    <string>/Users/jcastellanos/repos/mac-vm-pool/.venv/bin/python</string>
    <string>-m</string>
    <string>mac_vm_pool.server</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>MVP_TART_BIN</key><string>/Users/jcastellanos/repos/tart/.build/debug/tart</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>/tmp/mac-vm-pool.out.log</string>
  <key>StandardErrorPath</key><string>/tmp/mac-vm-pool.err.log</string>
</dict>
</plist>
```

- [ ] **Step 3: Register the MCP server with Claude Code (user scope)**

Run:
```bash
claude mcp add --transport http --scope user mac-vm-pool http://127.0.0.1:8000/mcp
claude mcp list
```
Expected: `mac-vm-pool` appears in the list. (If Step 1 showed a different URL, use that.)

- [ ] **Step 4: Add a README section**

Add to `README.md` a "Running the pool service (MCP)" section documenting: the foreground run command, installing the LaunchAgent (`cp deploy/com.accipiter.mac-vm-pool.plist ~/Library/LaunchAgents/ && launchctl load ~/Library/LaunchAgents/com.accipiter.mac-vm-pool.plist`), and the `claude mcp add` command from Step 3. State that the `mac-vm-test` skill requires this server running.

- [ ] **Step 5: Verify the tools are reachable**

Start the server (or load the LaunchAgent), then in a Claude Code session confirm `mcp__mac-vm-pool__acquire_vm` etc. are available. Record the outcome.

- [ ] **Step 6: Commit**

```bash
git add deploy/com.accipiter.mac-vm-pool.plist README.md
git commit -m "docs: register + run the mac-vm-pool MCP server"
```

---

### Task 5: Rewrite the skill's agentic flow onto MCP tools; retire bash scripts

**Files:**
- Modify: `~/.claude/skills/mac-vm-test/SKILL.md`
- Delete: `~/.claude/skills/mac-vm-test/scripts/vm-acquire.sh`, `~/.claude/skills/mac-vm-test/scripts/vm-release.sh`

**Interfaces:** none (skill docs). Manual verification.

- [ ] **Step 1: Delete the retired scripts**

```bash
rm ~/.claude/skills/mac-vm-test/scripts/vm-acquire.sh ~/.claude/skills/mac-vm-test/scripts/vm-release.sh
rmdir ~/.claude/skills/mac-vm-test/scripts 2>/dev/null || true
```

- [ ] **Step 2: Rewrite SKILL.md prerequisites + agentic flow**

Replace the "Prerequisites" and "The workflow" sections so acquire/release go through the MCP tools `mcp__mac-vm-pool__acquire_vm`, `mcp__mac-vm-pool__vm_status`, `mcp__mac-vm-pool__release_vm`, keeping the driving section on the `tart` CLI (`MVP_TART_BIN`). Concretely:
- Prerequisite: the `mac-vm-pool` MCP server is registered and running (link the README section); the entitled `tart` build is still needed for driving (`input`/`accessibility`) and set via `MVP_TART_BIN`.
- Flow: (1) call `acquire_vm(client_id=...)` → read `lease_id`, `vm_name`, `ip`; if the result is `{queued: true}`, stop and report the cap. (2) host build (`xcodebuild`/`swift build`). (3) install via `scp -i ~/.mac-vm-pool/id_ed25519` + launch via `"$MVP_TART_BIN" exec <vm_name> open /Users/admin/Apps/<App>.app`. (4) drive via `tart input`/`tart accessibility`. (5) observe/assert. (6) always `release_vm(lease_id)` — including on failure, after capturing an AX dump + screenshot.

- [ ] **Step 3: Verify no stale references remain**

Run:
```bash
grep -rn "vm-acquire.sh\|vm-release.sh\|MVP_MAX_VMS_PER_HOST" ~/.claude/skills/mac-vm-test/ || echo "clean"
```
Expected: `clean` (no references to the deleted scripts).

- [ ] **Step 4: Commit** (skill dir is not a git repo; record the change in the pool repo's README changelog note instead)

```bash
cd /Users/jcastellanos/repos/mac-vm-pool
git commit --allow-empty -m "chore: skill rewritten to MCP tools (skill dir is not version controlled)"
```

---

## Phase 2 — Human session

### Task 6: Config fields for human sessions

**Files:**
- Modify: `src/mac_vm_pool/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `Config.human_session_grace_seconds=30`, `Config.human_session_connect_timeout=600`, `Config.human_session_ttl=14400`, `Config.vnc_port=5900` (all int, env-overridable via `MVP_*`).

- [ ] **Step 1: Write the failing test**

Add to `tests/test_config.py`:

```python
def test_human_session_defaults():
    cfg = Config.load(None)
    assert cfg.human_session_grace_seconds == 30
    assert cfg.human_session_connect_timeout == 600
    assert cfg.human_session_ttl == 14400
    assert cfg.vnc_port == 5900
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_config.py::test_human_session_defaults -q`
Expected: FAIL — `AttributeError` on `human_session_grace_seconds`.

- [ ] **Step 3: Implement**

In `src/mac_vm_pool/config.py`, add to the `Config` dataclass (after `acquire_wait_timeout`):

```python
    human_session_grace_seconds: int = 30
    human_session_connect_timeout: int = 600
    human_session_ttl: int = 14400
    vnc_port: int = 5900
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_config.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/mac_vm_pool/config.py tests/test_config.py
git commit -m "feat(config): human-session timing + vnc port settings"
```

---

### Task 7: `human_session` helpers (deploy / launch / screen sharing / probe)

**Files:**
- Create: `src/mac_vm_pool/human_session.py`
- Test: `tests/test_human_session.py`

**Interfaces:**
- Produces:
  - `deploy_app(ip, ssh_key, app_path, runner=subprocess.run) -> None`
  - `launch_app(vm_name, app_basename, tart_bin, runner=subprocess.run) -> None`
  - `enable_screen_sharing(ip, ssh_key, runner=subprocess.run, password=None) -> str` (returns the VNC password)
  - `open_screen_sharing(ip, password, opener=subprocess.run) -> None`
  - `vnc_connection_probe(ip, ssh_key, vnc_port=5900, runner=subprocess.run) -> bool`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_human_session.py`:

```python
from unittest.mock import MagicMock
from mac_vm_pool import human_session as hs


def _ok(stdout="", rc=0):
    m = MagicMock(); m.returncode = rc; m.stdout = stdout; m.stderr = ""
    return m


def test_deploy_app_mkdirs_then_scps():
    calls = []
    def runner(args, **kw):
        calls.append(args); return _ok()
    hs.deploy_app("10.0.0.5", "/k/key", "/tmp/MyApp.app", runner=runner)
    joined = [" ".join(a) for a in calls]
    assert any("mkdir -p ~/Apps" in c for c in joined)
    assert any(c.startswith("scp ") and "MyApp.app" in c and "admin@10.0.0.5:~/Apps/" in c
               for c in joined)


def test_launch_app_opens_absolute_path():
    calls = []
    def runner(args, **kw):
        calls.append(args); return _ok()
    hs.launch_app("pool-1", "MyApp.app", "/bin/tart", runner=runner)
    assert calls[0] == ["/bin/tart", "exec", "pool-1", "open", "/Users/admin/Apps/MyApp.app"]


def test_enable_screen_sharing_runs_kickstart_and_returns_password():
    calls = []
    def runner(args, **kw):
        calls.append(args); return _ok()
    pw = hs.enable_screen_sharing("10.0.0.5", "/k/key", runner=runner, password="secret12")
    assert pw == "secret12"
    joined = " ".join(" ".join(a) for a in calls)
    assert "kickstart" in joined
    assert "-setvncpw -vncpw secret12" in joined
    assert "-activate" in joined and "-restart -agent" in joined


def test_enable_screen_sharing_generates_password_when_absent():
    def runner(args, **kw): return _ok()
    pw = hs.enable_screen_sharing("10.0.0.5", "/k/key", runner=runner)
    assert isinstance(pw, str) and len(pw) == 8


def test_open_screen_sharing_opens_vnc_url():
    calls = []
    def opener(args, **kw):
        calls.append(args); return _ok()
    hs.open_screen_sharing("10.0.0.5", "secret12", opener=opener)
    assert calls[0] == ["open", "vnc://:secret12@10.0.0.5"]


def test_vnc_connection_probe_true_when_established():
    def runner(args, **kw):
        return _ok(stdout="tcp4  0 0 10.0.0.5.5900 10.0.0.1.51000 ESTABLISHED\n", rc=0)
    assert hs.vnc_connection_probe("10.0.0.5", "/k/key") is True


def test_vnc_connection_probe_false_when_no_connection():
    def runner(args, **kw):
        return _ok(stdout="", rc=1)
    assert hs.vnc_connection_probe("10.0.0.5", "/k/key", runner=runner) is False
```

Note: `test_vnc_connection_probe_true_when_established` must inject its runner too — fix it to pass `runner=runner`:

```python
def test_vnc_connection_probe_true_when_established():
    def runner(args, **kw):
        return _ok(stdout="tcp4  0 0 10.0.0.5.5900 10.0.0.1.51000 ESTABLISHED\n", rc=0)
    assert hs.vnc_connection_probe("10.0.0.5", "/k/key", runner=runner) is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_human_session.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'mac_vm_pool.human_session'`.

- [ ] **Step 3: Implement**

Create `src/mac_vm_pool/human_session.py`:

```python
from __future__ import annotations
import os
import secrets
import subprocess

SSH_OPTS = ["-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null"]
KICKSTART = ("/System/Library/CoreServices/RemoteManagement/"
             "ARDAgent.app/Contents/Resources/kickstart")


def _ssh(ip: str, key: str, remote_cmd: str, runner) -> subprocess.CompletedProcess:
    return runner(
        ["ssh", "-i", os.path.expanduser(key), *SSH_OPTS, f"admin@{ip}", remote_cmd],
        capture_output=True, text=True, check=False,
    )


def deploy_app(ip: str, ssh_key: str, app_path: str, runner=subprocess.run) -> None:
    key = os.path.expanduser(ssh_key)
    _ssh(ip, key, "mkdir -p ~/Apps", runner)
    runner(
        ["scp", "-i", key, *SSH_OPTS, "-r", app_path, f"admin@{ip}:~/Apps/"],
        capture_output=True, text=True, check=True,
    )


def launch_app(vm_name: str, app_basename: str, tart_bin: str, runner=subprocess.run) -> None:
    runner(
        [tart_bin, "exec", vm_name, "open", f"/Users/admin/Apps/{app_basename}"],
        capture_output=True, text=True, check=True,
    )


def enable_screen_sharing(ip: str, ssh_key: str, runner=subprocess.run,
                          password: str | None = None) -> str:
    password = password or secrets.token_hex(4)   # 8 hex chars (VNC legacy pw max)
    cmd = (
        f"echo admin | sudo -S {KICKSTART} -activate -configure -access -on "
        f"-restart -agent -privs -all -clientopts -setvnclegacy -vnclegacy yes "
        f"-setvncpw -vncpw {password}"
    )
    _ssh(ip, os.path.expanduser(ssh_key), cmd, runner)
    return password


def open_screen_sharing(ip: str, password: str, opener=subprocess.run) -> None:
    opener(["open", f"vnc://:{password}@{ip}"], check=False)


def vnc_connection_probe(ip: str, ssh_key: str, vnc_port: int = 5900,
                         runner=subprocess.run) -> bool:
    cmd = f"netstat -an -p tcp | grep '\\.{vnc_port} ' | grep ESTABLISHED"
    r = _ssh(ip, os.path.expanduser(ssh_key), cmd, runner)
    return r.returncode == 0 and bool(r.stdout.strip())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_human_session.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/mac_vm_pool/human_session.py tests/test_human_session.py
git commit -m "feat(human_session): app deploy/launch, screen sharing, vnc probe"
```

---

### Task 8: `HumanSessionMonitor` disconnect state machine

**Files:**
- Modify: `src/mac_vm_pool/human_session.py`
- Test: `tests/test_human_session.py`

**Interfaces:**
- Consumes: a `probe() -> bool`, `on_close() -> None`, optional `keepalive() -> None`.
- Produces: `HumanSessionMonitor(probe, on_close, *, grace_seconds, connect_timeout, poll_interval=3.0, keepalive=None, clock=..., sleep=...)` with `_loop() -> str` (`"closed"` | `"never_connected"`) and `run()` that sets `self.outcome` and calls `on_close()`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_human_session.py`:

```python
def _seq_clock(values):
    it = iter(values)
    return lambda: next(it)


def test_monitor_never_connected():
    closed = []
    m = hs.HumanSessionMonitor(
        probe=lambda: False,
        on_close=lambda: closed.append(True),
        grace_seconds=30, connect_timeout=10, poll_interval=1,
        clock=_seq_clock([0.0, 0.0, 5.0, 11.0]), sleep=lambda s: None,
    )
    assert m._loop() == "never_connected"


def test_monitor_closes_after_disconnect_grace():
    # connect (True), then disconnect (False) held past grace
    probes = iter([True, False, False])
    m = hs.HumanSessionMonitor(
        probe=lambda: next(probes),
        on_close=lambda: None,
        grace_seconds=30, connect_timeout=100, poll_interval=1,
        clock=_seq_clock([0.0,           # phase1 start
                          0.0,           # phase2 first probe (True) -> keepalive
                          100.0,         # phase2 second probe (False) -> mark disconnect@100
                          200.0]),       # phase2 third probe (False) -> 200-100>=30 -> closed
        sleep=lambda s: None,
    )
    assert m._loop() == "closed"


def test_monitor_reconnect_resets_grace():
    # True, False (start grace), True (reset), then no more -> StopIteration ends loop
    probes = iter([True, False, True])
    kept = []
    m = hs.HumanSessionMonitor(
        probe=lambda: next(probes),
        on_close=lambda: None,
        grace_seconds=30, connect_timeout=100, poll_interval=1,
        keepalive=lambda: kept.append(True),
        clock=_seq_clock([0.0, 0.0, 10.0, 20.0]),
        sleep=lambda s: None,
    )
    import pytest
    with pytest.raises(StopIteration):
        m._loop()
    assert len(kept) == 2   # keepalive on both True probes


def test_monitor_run_invokes_on_close():
    closed = []
    m = hs.HumanSessionMonitor(
        probe=lambda: False,
        on_close=lambda: closed.append(True),
        grace_seconds=1, connect_timeout=0, poll_interval=1,
        clock=_seq_clock([0.0, 0.0, 1.0]), sleep=lambda s: None,
    )
    m.run()
    assert m.outcome == "never_connected"
    assert closed == [True]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_human_session.py -k monitor -q`
Expected: FAIL — `AttributeError: module 'mac_vm_pool.human_session' has no attribute 'HumanSessionMonitor'`.

- [ ] **Step 3: Implement**

Add to the top imports of `src/mac_vm_pool/human_session.py`:

```python
import threading
import time
```

Append the class to `src/mac_vm_pool/human_session.py`:

```python
class HumanSessionMonitor(threading.Thread):
    """Watch a guest's VNC connection; call on_close() when the human's Screen
    Sharing session ends (disconnect held for grace_seconds) or never starts
    (connect_timeout). Refresh the lease via keepalive() while connected."""

    def __init__(self, probe, on_close, *, grace_seconds, connect_timeout,
                 poll_interval=3.0, keepalive=None,
                 clock=time.monotonic, sleep=time.sleep):
        super().__init__(daemon=True)
        self._probe = probe
        self._on_close = on_close
        self._keepalive = keepalive or (lambda: None)
        self._grace = grace_seconds
        self._connect_timeout = connect_timeout
        self._poll = poll_interval
        self._clock = clock
        self._sleep = sleep
        self.outcome = None

    def run(self):
        self.outcome = self._loop()
        self._on_close()

    def _loop(self):
        # Phase 1: wait for the first connection.
        start = self._clock()
        while not self._probe():
            if self._clock() - start >= self._connect_timeout:
                return "never_connected"
            self._sleep(self._poll)
        # Phase 2: wait for disconnect held past the grace window.
        disconnected_since = None
        while True:
            if self._probe():
                self._keepalive()
                disconnected_since = None
            elif disconnected_since is None:
                disconnected_since = self._clock()
            elif self._clock() - disconnected_since >= self._grace:
                return "closed"
            self._sleep(self._poll)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_human_session.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/mac_vm_pool/human_session.py tests/test_human_session.py
git commit -m "feat(human_session): disconnect monitor with grace + keepalive"
```

---

### Task 9: `LeasePool.extend` (keep-alive for human sessions)

**Files:**
- Modify: `src/mac_vm_pool/pool.py`
- Test: `tests/test_pool.py`

**Interfaces:**
- Produces: `LeasePool.extend(lease_id: str, ttl: int) -> bool` — sets `expires_at = clock() + ttl`; returns False for unknown lease.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_pool.py`:

```python
def test_extend_bumps_expires_at():
    pool, _ = make_pool(clock_values=[1000.0, 1000.0, 5000.0])
    lease = pool.acquire("a", ttl=10)
    assert pool.extend(lease.lease_id, ttl=100) is True
    assert lease.expires_at == 5100.0            # 5000 (extend clock) + 100
    assert pool.extend("nope", ttl=100) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_pool.py::test_extend_bumps_expires_at -q`
Expected: FAIL — `AttributeError: 'LeasePool' object has no attribute 'extend'`.

- [ ] **Step 3: Implement**

In `src/mac_vm_pool/pool.py`, add after `status`:

```python
    def extend(self, lease_id: str, ttl: int) -> bool:
        with self._lock:
            lease = self._leases.get(lease_id)
            if lease is None:
                return False
            lease.expires_at = self.clock() + ttl
            return True
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_pool.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/mac_vm_pool/pool.py tests/test_pool.py
git commit -m "feat(pool): extend() to renew a lease's expiry"
```

---

### Task 10: `PoolService.start_human_session` + MCP tool registration

**Files:**
- Modify: `src/mac_vm_pool/server.py`
- Test: `tests/test_server.py`

**Interfaces:**
- Consumes: `human_session.*` (Tasks 7-8), `LeasePool.extend` (Task 9), `Config.human_session_*`/`vnc_port` (Task 6).
- Produces: `PoolService.start_human_session(lease_id, app_path=None, bundle_id=None) -> dict` returning `{"vnc_url", "vm_name", "ip", "monitoring": True}` or `{"found": False}`; registered as an MCP tool in `main()`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_server.py`:

```python
import os
from mac_vm_pool import human_session


def test_start_human_session_orchestrates(monkeypatch):
    svc = make_injected_server()
    lid = svc.acquire_vm("a")["lease_id"]
    ip = svc.vm_status(lid)["ip"]
    calls = {}
    monkeypatch.setattr(human_session, "deploy_app",
                        lambda *a, **k: calls.__setitem__("deploy", a))
    monkeypatch.setattr(human_session, "launch_app",
                        lambda *a, **k: calls.__setitem__("launch", a))
    monkeypatch.setattr(human_session, "enable_screen_sharing",
                        lambda *a, **k: "pw012345")
    monkeypatch.setattr(human_session, "open_screen_sharing",
                        lambda *a, **k: calls.__setitem__("open", a))

    started = {}

    class FakeMonitor:
        def __init__(self, *a, **k):
            started["kwargs"] = k
        def start(self):
            started["started"] = True

    monkeypatch.setattr(human_session, "HumanSessionMonitor", FakeMonitor)

    out = svc.start_human_session(lid, app_path="/tmp/MyApp.app")
    assert out == {"vnc_url": f"vnc://:pw012345@{ip}",
                   "vm_name": svc.vm_status(lid)["vm_name"],
                   "ip": ip, "monitoring": True}
    assert "deploy" in calls and "launch" in calls and "open" in calls
    assert started["started"] is True


def test_start_human_session_skips_deploy_without_app(monkeypatch):
    svc = make_injected_server()
    lid = svc.acquire_vm("a")["lease_id"]
    calls = {}
    monkeypatch.setattr(human_session, "deploy_app",
                        lambda *a, **k: calls.__setitem__("deploy", True))
    monkeypatch.setattr(human_session, "enable_screen_sharing",
                        lambda *a, **k: "pw012345")
    monkeypatch.setattr(human_session, "open_screen_sharing", lambda *a, **k: None)
    monkeypatch.setattr(human_session, "HumanSessionMonitor",
                        lambda *a, **k: type("M", (), {"start": lambda self: None})())
    out = svc.start_human_session(lid)
    assert out["monitoring"] is True
    assert "deploy" not in calls


def test_start_human_session_unknown_lease():
    svc = make_injected_server()
    assert svc.start_human_session("nope") == {"found": False}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_server.py -q`
Expected: FAIL — `AttributeError: 'PoolService' object has no attribute 'start_human_session'`.

- [ ] **Step 3: Implement**

In `src/mac_vm_pool/server.py`, add imports at the top:

```python
import os
from mac_vm_pool import human_session
```

Add `self._monitors` to `PoolService.__init__`:

```python
    def __init__(self, pool: LeasePool, cfg: Config | None = None):
        self.pool = pool
        self.cfg = cfg or Config.load(None)
        self._monitors: dict = {}
```

Add the method (after `list_pool`):

```python
    def start_human_session(self, lease_id: str, app_path: str | None = None,
                            bundle_id: str | None = None) -> dict:
        lease = self.pool.status(lease_id)
        if lease is None:
            return {"found": False}
        key = self.cfg.ssh_key_path
        if app_path:
            human_session.deploy_app(lease.ip, key, app_path)
            human_session.launch_app(lease.vm_name, os.path.basename(app_path),
                                     self.cfg.tart_bin)
        password = human_session.enable_screen_sharing(lease.ip, key)
        human_session.open_screen_sharing(lease.ip, password)
        self.pool.extend(lease_id, self.cfg.human_session_ttl)
        monitor = human_session.HumanSessionMonitor(
            probe=lambda: human_session.vnc_connection_probe(
                lease.ip, key, self.cfg.vnc_port),
            on_close=lambda: self.release_vm(lease_id),
            grace_seconds=self.cfg.human_session_grace_seconds,
            connect_timeout=self.cfg.human_session_connect_timeout,
            keepalive=lambda: self.pool.extend(lease_id, self.cfg.human_session_ttl),
        )
        monitor.start()
        self._monitors[lease_id] = monitor
        return {"vnc_url": f"vnc://:{password}@{lease.ip}",
                "vm_name": lease.vm_name, "ip": lease.ip, "monitoring": True}
```

In `main()`, register the tool alongside the others:

```python
    mcp.tool()(svc.start_human_session)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_server.py -q`
Expected: PASS.

- [ ] **Step 5: Run the full unit suite**

Run: `.venv/bin/python -m pytest -q -m "not integration"`
Expected: PASS (all tests).

- [ ] **Step 6: Commit**

```bash
git add src/mac_vm_pool/server.py tests/test_server.py
git commit -m "feat(server): start_human_session MCP tool + monitor wiring"
```

---

### Task 11: Skill human-testing section + README

**Files:**
- Modify: `~/.claude/skills/mac-vm-test/SKILL.md`
- Modify: `README.md`

**Interfaces:** none (docs). Manual verification.

- [ ] **Step 1: Add a "Manual / human testing" section to SKILL.md**

Document the human flow: (1) `acquire_vm(client_id=...)` → `lease_id`/`vm_name`/`ip`; (2) host build; (3) `start_human_session(lease_id, app_path="/abs/path/MyApp.app")`; (4) tell the user Screen Sharing has opened focused on the app and they can drive it with mouse/keyboard; (5) the VM auto-releases when they close the Screen Sharing window (grace ~30s), or they can ask Claude to `release_vm(lease_id)` now. Note copy/paste + drag-and-drop work natively over Screen Sharing.

- [ ] **Step 2: Add a README subsection**

Document `start_human_session` under the MCP tools list, and note the per-session Screen Sharing enablement + auto-teardown behavior and the `human_session_*` config knobs.

- [ ] **Step 3: Commit**

```bash
cd /Users/jcastellanos/repos/mac-vm-pool
git add README.md
git commit -m "docs: document human testing flow + start_human_session"
```

---

## Self-Review

**Spec coverage:**
- RPC-readiness gap → Tasks 1-3. ✓
- Headless boot → Tasks 1-2. ✓
- MCP register + run (HTTP) → Task 4. ✓
- Skill onto MCP tools + retire bash → Tasks 5, 11. ✓
- Config (grace/connect_timeout/ttl/vnc_port) → Task 6. ✓
- App deploy/launch + Screen Sharing enable + VNC open + probe → Task 7. ✓
- Disconnect monitor (grace, reconnect reset, keepalive, connect timeout) → Task 8. ✓
- Lease keep-alive/extend → Task 9. ✓
- `start_human_session` tool + wiring → Task 10. ✓
- TTL-vs-human-session fix (extend + monitor keepalive) → Tasks 9, 10. ✓
- Testing strategy (FakeHost + injected runner/clock/probe; integration gated) → every code task; integration remains the gated manual checklist in the spec. ✓

**Placeholder scan:** No TBD/TODO; every code step shows complete code and exact commands.

**Type consistency:** `wait_agent(name, timeout)`, `boot(name, graphics=False)`, `extend(lease_id, ttl) -> bool`, `start_human_session(lease_id, app_path, bundle_id) -> dict`, and the `human_session` function signatures are used identically across the tasks that define and consume them.

## Open items carried from the spec (verify during integration, not blocking unit work)
- `open vnc://` may still prompt on some macOS versions even with the legacy VNC password; account-auth fallback (`vnc://admin@ip`) is the backup.
- If the daemon is killed mid-session, the VM is reclaimed by `reconcile`/TTL rather than instantly.
- `kickstart` flags are pinned to the cirruslabs Sequoia base.
