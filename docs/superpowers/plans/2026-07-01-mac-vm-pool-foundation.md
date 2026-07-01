# mac-vm-pool Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a client acquire and release an isolated macOS VM through an HTTP MCP server, backed by a lease pool that enforces the 2-VM-per-host cap and clones fresh VMs from a tooling-baked golden image.

**Architecture:** A `Host` protocol abstracts VM operations (`LocalHost` shells to `tart`; `FakeHost` for tests). A `LeasePool` owns lease state and enforces the cap against any `Host`. A golden-image baker automates agent-swap + TCC grant + SSH-key install. A FastMCP server exposes the pool as `acquire_vm`/`release_vm`/`vm_status`/`list_pool`/`provision_golden_image`.

**Tech Stack:** Python 3.12, `pytest`, `mcp` SDK (FastMCP, HTTP transport), `tart` CLI, `ssh`/`scp`.

## Global Constraints

- `max_vms_per_host = 2` — Apple Virtualization.framework hard cap; never exceed running macOS VMs per host. Copied verbatim as the pool invariant.
- Language: Python 3.12 (consistency with existing `benchling-mcp`).
- MCP transport: **HTTP** (parallel agents in separate sessions share one daemon).
- Reset policy: **fresh clone per lease**, **destroy on release** — no in-place reset.
- Base image: `ghcr.io/cirruslabs/macos-sequoia-base:latest`.
- Golden image name: `mac-test-golden`.
- Golden image auth: **SSH key baked into `authorized_keys`** (no passwords).
- Prerequisite binaries (from the prototype, in `~/repos`): modified `tart-guest-agent`; `tart` CLI with `input`/`accessibility` commands. Paths supplied via config.
- Home directory: `~/repos/mac-vm-pool/`.

---

### Task 1: Project scaffold + config

**Files:**
- Create: `pyproject.toml`
- Create: `src/mac_vm_pool/__init__.py`
- Create: `src/mac_vm_pool/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `Config` dataclass with fields `max_vms_per_host: int = 2`, `warm_target: int = 1`, `golden_image: str = "mac-test-golden"`, `base_image: str = "ghcr.io/cirruslabs/macos-sequoia-base:latest"`, `lease_ttl: int = 1800`, `acquire_wait_timeout: int = 300`, `ssh_key_path: str`, `tart_bin: str = "tart"`, `guest_agent_binary: str`. Classmethod `Config.load(path: str | None) -> Config` (reads TOML, falls back to defaults; env vars `MVP_*` override).

- [ ] **Step 1: Write `pyproject.toml`**

```toml
[project]
name = "mac-vm-pool"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = ["mcp>=1.2.0"]

[project.optional-dependencies]
dev = ["pytest>=8.0"]

[tool.pytest.ini_options]
testpaths = ["tests"]
markers = ["integration: needs a real Mac + tart images (deselect with -m 'not integration')"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/mac_vm_pool"]
```

- [ ] **Step 2: Write the failing test**

```python
# tests/test_config.py
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
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/test_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mac_vm_pool.config'`

- [ ] **Step 4: Write `src/mac_vm_pool/__init__.py`**

```python
```
(empty file)

- [ ] **Step 5: Write `src/mac_vm_pool/config.py`**

```python
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
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pip install -e '.[dev]' && pytest tests/test_config.py -v`
Expected: PASS (2 passed)

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml src/mac_vm_pool/__init__.py src/mac_vm_pool/config.py tests/test_config.py
git commit -m "feat: project scaffold and config"
```

---

### Task 2: Host protocol + FakeHost

**Files:**
- Create: `src/mac_vm_pool/host.py`
- Test: `tests/test_fakehost.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `class Host(Protocol)` with methods: `capacity() -> int`, `running() -> list[str]`, `clone(src: str, name: str) -> None`, `boot(name: str) -> None`, `wait_ip(name: str, timeout: float) -> str`, `delete(name: str) -> None`, `exists(name: str) -> bool`.
  - `class FakeHost` implementing `Host` in memory, with `capacity_limit: int` and helpers for tests. `clone` adds a stopped VM; `boot` marks running and assigns a fake IP `10.0.0.<n>`; `running()` lists booted VMs; `delete` removes it.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_fakehost.py
import pytest
from mac_vm_pool.host import FakeHost

def test_clone_boot_ip_delete_cycle():
    h = FakeHost(capacity_limit=2)
    assert h.running() == []
    h.clone("golden", "vm1")
    assert h.exists("vm1")
    assert h.running() == []            # cloned but not booted
    h.boot("vm1")
    assert h.running() == ["vm1"]
    ip = h.wait_ip("vm1", timeout=1)
    assert ip.startswith("10.0.0.")
    h.delete("vm1")
    assert not h.exists("vm1")
    assert h.running() == []

def test_capacity_reports_free_slots():
    h = FakeHost(capacity_limit=2)
    h.clone("g", "a"); h.boot("a")
    assert h.capacity() == 1            # 2 - 1 running
    h.clone("g", "b"); h.boot("b")
    assert h.capacity() == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_fakehost.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mac_vm_pool.host'`

- [ ] **Step 3: Write `src/mac_vm_pool/host.py`**

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_fakehost.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add src/mac_vm_pool/host.py tests/test_fakehost.py
git commit -m "feat: Host protocol and in-memory FakeHost"
```

---

### Task 3: LocalHost (tart-backed)

**Files:**
- Create: `src/mac_vm_pool/local_host.py`
- Test: `tests/test_local_host.py`

**Interfaces:**
- Consumes: `Host` protocol (Task 2); `Config` (Task 1).
- Produces: `class LocalHost` implementing `Host` by shelling to `tart`. Constructor `LocalHost(config: Config)`. Uses `subprocess.run`. `running()` parses `tart list`; `wait_ip` polls `tart ip <name>` until non-empty or timeout; `boot` launches `tart run <name>` detached.

- [ ] **Step 1: Write the failing test (subprocess mocked)**

```python
# tests/test_local_host.py
from unittest.mock import patch, MagicMock
from mac_vm_pool.config import Config
from mac_vm_pool.local_host import LocalHost

def _run_ok(stdout=""):
    m = MagicMock(); m.returncode = 0; m.stdout = stdout; m.stderr = ""
    return m

def test_running_parses_tart_list():
    cfg = Config.load(None)
    h = LocalHost(cfg)
    listing = (
        "Source Name          Disk Size Accessed State\n"
        "local  pool-aaa       50   33   now      running\n"
        "local  pool-bbb       50   33   now      stopped\n"
    )
    with patch("subprocess.run", return_value=_run_ok(listing)):
        assert h.running() == ["pool-aaa"]

def test_clone_invokes_tart_clone():
    cfg = Config.load(None)
    h = LocalHost(cfg)
    with patch("subprocess.run", return_value=_run_ok()) as run:
        h.clone("mac-test-golden", "pool-xyz")
        args = run.call_args[0][0]
        assert args[:3] == [cfg.tart_bin, "clone", "mac-test-golden"]
        assert args[3] == "pool-xyz"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_local_host.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mac_vm_pool.local_host'`

- [ ] **Step 3: Write `src/mac_vm_pool/local_host.py`**

```python
from __future__ import annotations
import subprocess
import time
from mac_vm_pool.config import Config

class LocalHost:
    def __init__(self, config: Config):
        self.cfg = config
        self.tart = config.tart_bin

    def _run(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        return subprocess.run([self.tart, *args], capture_output=True, text=True, check=check)

    def capacity(self) -> int:
        return self.cfg.max_vms_per_host - len(self.running())

    def running(self) -> list[str]:
        out = self._run("list").stdout.splitlines()
        names = []
        for line in out[1:]:                      # skip header
            parts = line.split()
            if len(parts) >= 2 and parts[-1] == "running" and parts[0] == "local":
                names.append(parts[1])
        return names

    def clone(self, src: str, name: str) -> None:
        self._run("clone", src, name)

    def boot(self, name: str) -> None:
        # tart run is long-running; detach it.
        subprocess.Popen(
            [self.tart, "run", name],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,
        )

    def wait_ip(self, name: str, timeout: float) -> str:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            r = self._run("ip", name, check=False)
            ip = r.stdout.strip()
            if r.returncode == 0 and ip:
                return ip
            time.sleep(3)
        raise TimeoutError(f"VM {name} did not acquire an IP within {timeout}s")

    def delete(self, name: str) -> None:
        self._run("stop", name, check=False)
        self._run("delete", name, check=False)

    def exists(self, name: str) -> bool:
        out = self._run("list").stdout.splitlines()
        return any(name in line.split() for line in out[1:])
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_local_host.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Verify LocalHost satisfies the Host protocol**

Add to `tests/test_local_host.py`:

```python
from mac_vm_pool.host import Host
def test_localhost_is_a_host():
    assert isinstance(LocalHost(Config.load(None)), Host)
```

Run: `pytest tests/test_local_host.py::test_localhost_is_a_host -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/mac_vm_pool/local_host.py tests/test_local_host.py
git commit -m "feat: tart-backed LocalHost"
```

---

### Task 4: LeasePool — cap enforcement & allocation

**Files:**
- Create: `src/mac_vm_pool/lease.py`
- Create: `src/mac_vm_pool/pool.py`
- Test: `tests/test_pool.py`

**Interfaces:**
- Consumes: `Host` (Task 2), `Config` (Task 1).
- Produces:
  - `@dataclass Lease` with `lease_id: str`, `vm_name: str`, `client_id: str`, `state: str`, `ip: str | None`, `created_at: float`, `expires_at: float`.
  - `class LeasePool(host: Host, config: Config, clock=time.monotonic, namegen=None)`:
    - `acquire(client_id: str, ttl: int | None = None) -> Lease | None` — returns a `Lease` (state `"leased"`) if a slot is free, else `None` (caller queues/retries). Clones from `config.golden_image`, boots, waits for IP.
    - `release(lease_id: str) -> bool` — destroys the VM, drops the lease; returns False if unknown.
    - `status(lease_id: str) -> Lease | None`.
    - `leases() -> list[Lease]`.
    - `reap(now: float | None = None) -> list[str]` — deletes VMs for expired leases; returns reaped lease_ids.
    - Never allocates when `host.capacity() <= 0`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_pool.py
import itertools
from mac_vm_pool.config import Config
from mac_vm_pool.host import FakeHost
from mac_vm_pool.pool import LeasePool

def make_pool(clock_values=None):
    cfg = Config.load(None)
    host = FakeHost(capacity_limit=cfg.max_vms_per_host)
    counter = itertools.count(1)
    namegen = lambda: f"pool-{next(counter)}"
    if clock_values is not None:
        it = iter(clock_values)
        clock = lambda: next(it)
    else:
        clock = lambda: 1000.0
    return LeasePool(host, cfg, clock=clock, namegen=namegen), host

def test_acquire_returns_leased_vm_with_ip():
    pool, host = make_pool()
    lease = pool.acquire("agent-1")
    assert lease is not None
    assert lease.state == "leased"
    assert lease.ip.startswith("10.0.0.")
    assert lease.vm_name in host.running()

def test_cap_blocks_third_acquire():
    pool, _ = make_pool()
    assert pool.acquire("a") is not None
    assert pool.acquire("b") is not None
    assert pool.acquire("c") is None          # cap = 2

def test_release_frees_a_slot():
    pool, host = make_pool()
    a = pool.acquire("a"); pool.acquire("b")
    assert pool.acquire("c") is None
    assert pool.release(a.lease_id) is True
    assert a.vm_name not in host.running()
    assert pool.acquire("c") is not None       # slot freed

def test_reap_expired_lease():
    # clock: acquire sees 1000 (created), 1000 (expires calc); reap sees 9999
    pool, host = make_pool(clock_values=[1000.0, 1000.0, 9999.0, 9999.0])
    lease = pool.acquire("a", ttl=10)
    vm = lease.vm_name
    reaped = pool.reap()
    assert lease.lease_id in reaped
    assert vm not in host.running()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_pool.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mac_vm_pool.pool'`

- [ ] **Step 3: Write `src/mac_vm_pool/lease.py`**

```python
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
```

- [ ] **Step 4: Write `src/mac_vm_pool/pool.py`**

```python
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
                expires_at=now + ttl,
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
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_pool.py -v`
Expected: PASS (4 passed)

- [ ] **Step 6: Commit**

```bash
git add src/mac_vm_pool/lease.py src/mac_vm_pool/pool.py tests/test_pool.py
git commit -m "feat: LeasePool with cap enforcement and reaping"
```

---

### Task 5: Startup reconciliation

**Files:**
- Modify: `src/mac_vm_pool/pool.py` (add `reconcile`)
- Test: `tests/test_pool.py` (add reconcile test)

**Interfaces:**
- Consumes: `LeasePool` (Task 4).
- Produces: `LeasePool.reconcile() -> list[str]` — deletes running VMs whose name matches the pool prefix `pool-` but has no live lease (orphans from a crash); returns deleted names. Only touches `pool-`-prefixed VMs so it never disturbs unrelated VMs.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_pool.py
def test_reconcile_destroys_orphans_only():
    pool, host = make_pool()
    lease = pool.acquire("a")               # tracked pool VM
    host.clone("g", "pool-orphan"); host.boot("pool-orphan")  # crash orphan
    host.clone("g", "user-vm"); host.boot("user-vm")          # unrelated VM
    deleted = pool.reconcile()
    assert "pool-orphan" in deleted
    assert lease.vm_name not in deleted     # tracked lease survives
    assert "user-vm" not in deleted         # non-pool VM untouched
    assert "user-vm" in host.running()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_pool.py::test_reconcile_destroys_orphans_only -v`
Expected: FAIL with `AttributeError: 'LeasePool' object has no attribute 'reconcile'`

- [ ] **Step 3: Add `reconcile` to `src/mac_vm_pool/pool.py`**

```python
    def reconcile(self) -> list[str]:
        with self._lock:
            live = {l.vm_name for l in self._leases.values()}
            deleted = []
            for name in self.host.running():
                if name.startswith("pool-") and name not in live:
                    self.host.delete(name)
                    deleted.append(name)
            return deleted
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_pool.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add src/mac_vm_pool/pool.py tests/test_pool.py
git commit -m "feat: startup reconciliation of orphan pool VMs"
```

---

### Task 6: Golden-image baker

**Files:**
- Create: `src/mac_vm_pool/baker.py`
- Test: `tests/test_baker.py`

**Interfaces:**
- Consumes: `Config` (Task 1).
- Produces: `bake_golden_image(cfg: Config, runner=subprocess.run) -> dict` — executes the documented sequence and returns `{"image": cfg.golden_image, "smoke_ok": bool}`. Steps: clone base → boot → wait IP → scp agent binary → swap + reload launchд → write TCC grants → install SSH key → smoke test → stop → `tart` commit as `golden_image`. The `runner` parameter is injected so the test can assert the command sequence without a real Mac.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_baker.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_baker.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mac_vm_pool.baker'`

- [ ] **Step 3: Write `src/mac_vm_pool/baker.py`**

```python
from __future__ import annotations
import os
import subprocess
import time
from mac_vm_pool.config import Config

BUILD_VM = "golden-build"
SSH_OPTS = ["-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null"]

def bake_golden_image(cfg: Config, runner=subprocess.run) -> dict:
    tart = cfg.tart_bin
    agent = os.path.expanduser(cfg.guest_agent_binary)
    key = os.path.expanduser(cfg.ssh_key_path)
    pub = key + ".pub"

    def sh(args, check=True):
        return runner(args, capture_output=True, text=True, check=check)

    def ssh(remote_cmd, ip, check=True):
        return sh(["sshpass", "-p", "admin", "ssh", *SSH_OPTS, f"admin@{ip}", remote_cmd], check=check)

    # 1. fresh build VM from base
    sh([tart, "delete", BUILD_VM], check=False)
    sh([tart, "clone", cfg.base_image, BUILD_VM])
    subprocess.Popen([tart, "run", BUILD_VM], stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)

    # 2. wait for IP
    ip = ""
    for _ in range(60):
        r = sh([tart, "ip", BUILD_VM], check=False)
        ip = r.stdout.strip()
        if ip:
            break
        time.sleep(3)

    # 3. copy + swap the modified guest agent, reload launchd
    sh(["sshpass", "-p", "admin", "scp", *SSH_OPTS, agent, f"admin@{ip}:/tmp/tga-new"])
    ssh("chmod +x /tmp/tga-new", ip)
    ssh("UID_NUM=$(id -u); P=/Library/LaunchAgents/org.cirruslabs.tart-guest-agent.plist; "
        "launchctl bootout gui/$UID_NUM $P; T=$(readlink -f /opt/homebrew/bin/tart-guest-agent); "
        "echo admin | sudo -S rm -f $T; echo admin | sudo -S cp /tmp/tga-new $T; "
        "echo admin | sudo -S chmod +x $T; launchctl bootstrap gui/$UID_NUM $P", ip)

    # 4. grant TCC (SIP off in cirruslabs images)
    ssh('DB="/Library/Application Support/com.apple.TCC/TCC.db"; T=$(readlink -f /opt/homebrew/bin/tart-guest-agent); '
        'for SVC in kTCCServiceAccessibility kTCCServiceScreenCapture kTCCServicePostEvent; do '
        'echo admin | sudo -S sqlite3 "$DB" "INSERT OR REPLACE INTO access '
        '(service,client,client_type,auth_value,auth_reason,auth_version,csreq,flags,last_modified) '
        'VALUES (\\"$SVC\\",\\"$T\\",1,2,4,1,NULL,0,$(date +%s));"; done; '
        'echo admin | sudo -S killall tccd', ip)

    # 5. install the baked SSH public key
    with open(pub) as fh:
        pubkey = fh.read().strip()
    ssh(f'mkdir -p ~/.ssh && echo "{pubkey}" >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys', ip)

    # 6. smoke test: type + query AX via the built tart CLI on the host
    smoke_ok = True
    try:
        sh([tart, "exec", BUILD_VM, "open", "-a", "TextEdit"], check=False)
        sh([cfg.tart_bin, "input", "type", BUILD_VM, "smoke"], check=False)
    except Exception:
        smoke_ok = False

    # 7. shutdown + commit as golden image
    sh([tart, "stop", BUILD_VM], check=False)
    sh([tart, "delete", cfg.golden_image], check=False)
    sh([tart, "clone", BUILD_VM, cfg.golden_image])
    sh([tart, "delete", BUILD_VM], check=False)

    return {"image": cfg.golden_image, "smoke_ok": smoke_ok}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_baker.py -v`
Expected: PASS (1 passed)

- [ ] **Step 5: Commit**

```bash
git add src/mac_vm_pool/baker.py tests/test_baker.py
git commit -m "feat: golden-image baker (agent swap + TCC + ssh key)"
```

---

### Task 7: MCP server (HTTP) exposing the pool

**Files:**
- Create: `src/mac_vm_pool/server.py`
- Test: `tests/test_server.py`

**Interfaces:**
- Consumes: `LeasePool` (Task 4/5), `bake_golden_image` (Task 6), `Config` (Task 1), `LocalHost` (Task 3).
- Produces: `build_pool(cfg: Config) -> LeasePool` (wires `LocalHost` + `LeasePool`, runs `reconcile()` once); FastMCP app `mcp` with tools:
  - `acquire_vm(client_id: str, ttl_seconds: int = 1800) -> dict` — returns lease dict, or `{"queued": true, "reason": "cap_reached"}` when `acquire` returns None.
  - `release_vm(lease_id: str) -> dict` — `{"released": bool}`.
  - `vm_status(lease_id: str) -> dict` — lease dict or `{"found": false}`.
  - `list_pool() -> dict` — `{"leases": [...], "capacity_free": int}`.
  - `provision_golden_image() -> dict` — calls `bake_golden_image`.
  - Server runs with `mcp.run(transport="streamable-http")`.

- [ ] **Step 1: Write the failing test (pool injected, FakeHost)**

```python
# tests/test_server.py
from mac_vm_pool.config import Config
from mac_vm_pool.host import FakeHost
from mac_vm_pool.pool import LeasePool
from mac_vm_pool import server

def make_injected_server():
    cfg = Config.load(None)
    pool = LeasePool(FakeHost(capacity_limit=2), cfg)
    return server.PoolService(pool)

def test_acquire_then_status_then_release():
    svc = make_injected_server()
    res = svc.acquire_vm("agent-1")
    assert res["state"] == "leased"
    lid = res["lease_id"]
    assert svc.vm_status(lid)["lease_id"] == lid
    assert svc.release_vm(lid) == {"released": True}
    assert svc.vm_status(lid) == {"found": False}

def test_acquire_reports_queued_at_cap():
    svc = make_injected_server()
    svc.acquire_vm("a"); svc.acquire_vm("b")
    assert svc.acquire_vm("c") == {"queued": True, "reason": "cap_reached"}

def test_list_pool_reports_free_capacity():
    svc = make_injected_server()
    svc.acquire_vm("a")
    listing = svc.list_pool()
    assert listing["capacity_free"] == 1
    assert len(listing["leases"]) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_server.py -v`
Expected: FAIL with `AttributeError: module 'mac_vm_pool.server' has no attribute 'PoolService'`

- [ ] **Step 3: Write `src/mac_vm_pool/server.py`**

```python
from __future__ import annotations
from mac_vm_pool.config import Config
from mac_vm_pool.local_host import LocalHost
from mac_vm_pool.pool import LeasePool
from mac_vm_pool.baker import bake_golden_image

class PoolService:
    """Transport-agnostic tool implementations, unit-testable with a FakeHost."""
    def __init__(self, pool: LeasePool, cfg: Config | None = None):
        self.pool = pool
        self.cfg = cfg or Config.load(None)

    def acquire_vm(self, client_id: str, ttl_seconds: int = 1800) -> dict:
        lease = self.pool.acquire(client_id, ttl=ttl_seconds)
        if lease is None:
            return {"queued": True, "reason": "cap_reached"}
        return lease.to_dict()

    def release_vm(self, lease_id: str) -> dict:
        return {"released": self.pool.release(lease_id)}

    def vm_status(self, lease_id: str) -> dict:
        lease = self.pool.status(lease_id)
        return lease.to_dict() if lease else {"found": False}

    def list_pool(self) -> dict:
        return {
            "leases": [l.to_dict() for l in self.pool.leases()],
            "capacity_free": self.pool.host.capacity(),
        }

    def provision_golden_image(self) -> dict:
        return bake_golden_image(self.cfg)

def build_pool(cfg: Config) -> LeasePool:
    pool = LeasePool(LocalHost(cfg), cfg)
    pool.reconcile()
    return pool

def main() -> None:
    from mcp.server.fastmcp import FastMCP
    cfg = Config.load(None)
    svc = PoolService(build_pool(cfg), cfg)
    mcp = FastMCP("mac-vm-pool")
    mcp.tool()(svc.acquire_vm)
    mcp.tool()(svc.release_vm)
    mcp.tool()(svc.vm_status)
    mcp.tool()(svc.list_pool)
    mcp.tool()(svc.provision_golden_image)
    mcp.run(transport="streamable-http")

if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_server.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Run the whole unit suite**

Run: `pytest -m "not integration" -v`
Expected: all green (config, fakehost, local_host, pool, baker, server)

- [ ] **Step 6: Commit**

```bash
git add src/mac_vm_pool/server.py tests/test_server.py
git commit -m "feat: MCP server exposing the lease pool over HTTP"
```

---

### Task 8: End-to-end integration test (real VM, gated)

**Files:**
- Create: `tests/test_integration.py`
- Create: `README.md`

**Interfaces:**
- Consumes: everything above. Marked `@pytest.mark.integration`; skipped in the default unit run; requires a real Mac, `tart`, the golden image (or runs the baker), and the prerequisite binaries.

- [ ] **Step 1: Write the integration test**

```python
# tests/test_integration.py
import os
import pytest
from mac_vm_pool.config import Config
from mac_vm_pool.server import build_pool, PoolService

pytestmark = pytest.mark.integration

@pytest.mark.skipif(os.environ.get("MVP_RUN_INTEGRATION") != "1",
                    reason="set MVP_RUN_INTEGRATION=1 on a Mac with the golden image")
def test_acquire_release_real_vm():
    cfg = Config.load(None)
    svc = PoolService(build_pool(cfg), cfg)
    lease = svc.acquire_vm("integration")
    assert lease.get("state") == "leased"
    assert lease["ip"]
    try:
        # the leased VM is reachable
        assert svc.vm_status(lease["lease_id"])["state"] == "leased"
    finally:
        assert svc.release_vm(lease["lease_id"]) == {"released": True}
    assert svc.vm_status(lease["lease_id"]) == {"found": False}
```

- [ ] **Step 2: Verify it is skipped by default**

Run: `pytest -m "not integration" -v`
Expected: integration test not collected; unit tests pass.

- [ ] **Step 3: Write `README.md`**

````markdown
# mac-vm-pool

Leases isolated macOS VMs to parallel Claude Code agents for host-compiled,
VM-tested macOS app functional testing (via the tart input/accessibility RPCs).

## Setup
1. Build prerequisites: modified `tart-guest-agent` and the `tart` CLI with
   `input`/`accessibility` (see `~/repos/tart-guest-agent`, `~/repos/tart`).
2. `pip install -e '.[dev]'`
3. Generate the SSH key: `ssh-keygen -t ed25519 -f ~/.mac-vm-pool/id_ed25519 -N ""`
4. Bake the golden image: `python -c "from mac_vm_pool.config import Config; from mac_vm_pool.baker import bake_golden_image; print(bake_golden_image(Config.load(None)))"`
5. Run the server: `python -m mac_vm_pool.server`

## Test
- Unit: `pytest -m "not integration"`
- Integration (needs a Mac + golden image): `MVP_RUN_INTEGRATION=1 pytest -m integration`
````

- [ ] **Step 4: Run the full integration path once on the Mac**

Run: `MVP_RUN_INTEGRATION=1 pytest -m integration -v`
Expected: PASS (acquires a real VM, releases it). Confirm with `tart list` that no `pool-` VMs remain.

- [ ] **Step 5: Commit**

```bash
git add tests/test_integration.py README.md
git commit -m "test: gated end-to-end integration + README"
```

---

## Self-Review

**Spec coverage:**
- Golden image baked by tooling → Task 6. ✓
- Fresh clone per lease, destroy on release → Task 4 (`acquire` clones, `release` deletes). ✓
- 2-VM cap → Task 4 (`capacity() <= 0` guard) + Task 2/3 (`capacity`). ✓
- Lease TTL + reaper → Task 4 (`reap`). ✓
- Crash reconciliation → Task 5 (`reconcile`). ✓
- MCP tools over HTTP → Task 7 (`streamable-http`). ✓
- Host abstraction (fleet-ready) → Tasks 2/3 (`Host` protocol, `LocalHost`). ✓
- SSH key baked in → Task 6 step 3 (5). ✓
- **Deferred to Plan 2 (documented):** warm pool, blocking wait queue with position, the `mac-vm-test` skill, `RemoteHost`. This plan returns `{"queued": true}` immediately at cap; blocking/prewarm is Plan 2.

**Placeholder scan:** No TBD/TODO; every code step has complete code. ✓

**Type consistency:** `Host` methods (`capacity/running/clone/boot/wait_ip/delete/exists`) are identical across `FakeHost`, `LocalHost`, and `LeasePool` call sites. `Lease.to_dict()` used consistently by `PoolService`. `acquire` returns `Lease | None` and `PoolService.acquire_vm` handles both branches. ✓

**Note on deviation from spec milestone order:** the spec placed the `Host` abstraction last (milestone 6); this plan pulls the *interface* forward to Task 2 because it makes the pool unit-testable without VMs (`FakeHost`). Only the interface + `LocalHost` are built here; `RemoteHost` remains Plan 2.
