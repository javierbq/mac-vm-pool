# mac-vm human testing + pool-service consolidation — Design Spec

**Date:** 2026-07-02
**Status:** Implemented; revised 2026-07-06 after live testing
**Extends:** [`2026-07-01-mac-vm-pool-design.md`](2026-07-01-mac-vm-pool-design.md)

## Revision 2026-07-06 — Screen Sharing hand-off redesign (supersedes the Screen Sharing details below)

Live testing showed the original hand-off was unusable, for reasons that were
integration-level (not unit-testable). This revision supersedes the
`enable_screen_sharing` / legacy-VNC-password / auto-teardown-on-close details in
the component and data-flow sections below:

- **Auth (was broken):** the original per-session `enable_screen_sharing` set a
  *legacy VNC password* (`-setvnclegacy -setvncpw`) and returned `vnc://:<pw>@ip`.
  Modern macOS Screen Sharing negotiates **account auth** and rejects that →
  "Authentication failed." **Fix:** enable Screen Sharing with **account access**
  for `admin` **once at bake time** (in `baker.py`); `start_human_session` now
  just opens `vnc://admin:admin@ip`. `enable_screen_sharing` is removed.
- **No per-session guest reconfiguration:** the per-session `kickstart -restart
  -agent` destabilized live VMs' networking/SSH. Moving Screen Sharing to bake
  time removes it from the session path entirely. **Requires a one-time re-bake.**
- **Teardown:** the monitor's connection probe (an ESTABLISHED `:5900` socket)
  couldn't tell a real session from a failed-auth handshake, so a failed connect
  looked like connect→disconnect and reclaimed the VM ~grace seconds later.
  **Fix:** teardown is now **primarily explicit** (`release_vm`); the monitor is a
  backstop that requires a connection sustained across
  `human_session_connect_confirmations` (default 2) polls before it can arm, and
  a failed hand-off **keeps** the VM (does not release) for inspection/retry.
- **New config:** `human_session_connect_confirmations` (2), `vnc_user`
  ("admin"), `vnc_password` ("admin"). `start_human_session` return adds
  `teardown`.

## Purpose

Add a **human-driven manual-testing mode** to the macOS-app testing workflow: on
request, spin up a fresh isolated VM, build/install/launch the app into it, and
hand the keyboard and mouse to a human via macOS **Screen Sharing** — then tear
the VM down automatically when the human closes the session.

Doing this correctly forces a second, larger change the user has asked for:
**route all VM lifecycle through the `mac-vm-pool` service** instead of the
`mac-vm-test` skill's parallel bash reimplementation. The
[2026-07-01 design](2026-07-01-mac-vm-pool-design.md) always intended the skill
to consume the pool's MCP tools ("MCP tools plus the built `tart` CLI"); the
shipped skill diverged and reimplemented acquire/release in bash. This spec
realigns the skill with that design and extends the pool service with the
human-session capability.

## Decisions locked in (from brainstorming)

| Decision | Choice |
|----------|--------|
| Display / control transport | **macOS Screen Sharing** (`vnc://` to the guest) |
| Teardown trigger | **Auto-destroy when the human's Screen Sharing session closes** |
| Handoff scope | Agent **builds + installs + launches** the app, then hands off |
| Runtime | **Everything routes through the pool service** (retire the skill's bash acquire/release) |
| Screen Sharing enablement | **Per-session** over SSH (`kickstart`); golden image unchanged, no re-bake |
| Disconnect detection | **Guest-side monitor** of the `:5900` connection, with a grace period |
| MCP server | **Registered in Claude Code and kept running** (persistent, HTTP transport) |
| Spec shape | **One spec, two implementation phases** (parity refactor → human session) |

## Scope

- Extend `PoolService` with a `start_human_session` MCP tool and supporting logic.
- Close the guest-agent RPC-readiness gap in `acquire` (see gaps below).
- Boot VMs headless (`--no-graphics`) in the pool — the service never needs
  tart's built-in window; the human uses Screen Sharing, the agent uses RPCs.
- Rewrite `mac-vm-test` (SKILL.md) so both the agentic and human paths drive VM
  lifecycle through the pool's MCP tools; retire `vm-acquire.sh` / `vm-release.sh`.
- Register the `mac-vm-pool` MCP server in Claude Code and document running it.

## Non-goals

- Proxying low-level driving (`tart input` / `tart accessibility`) through the
  pool service. Those remain direct `tart` CLI calls, exactly as the 2026-07-01
  design intended. "Everything through the pool service" means **VM lifecycle and
  session shaping**, not every keystroke.
- Compiling inside the VM (host build stays with the agent — it's project-specific).
- Warm pool / wait queue / `renew_lease` (still unimplemented from the prior
  spec; out of scope here, not required by human testing).
- Multi-host fleet implementation.
- The built-in tart window and `--vnc-experimental` transports (Screen Sharing chosen).

## Current-state gap analysis

The shipped `mac-vm-pool` is a subset of its own design, and the skill bypasses it:

1. **`LeasePool.acquire` does not wait for guest-agent RPC readiness.** It does
   clone → boot → `wait_ip` only. The skill's `vm-acquire.sh` had to add a wait
   for `tart exec <vm> true` to succeed (the README's "Plan-2" launchd race).
   Without this, the pool service is not a safe drop-in for the skill.
2. **No human-session capability** — no Screen Sharing enable, no headless boot
   option (`LocalHost.boot` always runs `tart run` with graphics), no VNC open,
   no disconnect monitor, no app install/launch.
3. **Not registered / not running** in Claude Code — nothing can call the tools.
4. **The skill is bash, not MCP-aware** — it reimplements acquire/release and
   must be rewritten to call the tools.

## Architecture (target)

```
┌───────────────────────────────────────────────────────────────┐
│  mac-vm-pool daemon (Python, long-running, HTTP MCP)           │
│  • lease table (in-memory) • 2-VM cap • TTL reaper/reconcile   │
│  • Host abstraction (LocalHost → tart)                         │
│  tools: acquire_vm · release_vm · vm_status · list_pool ·      │
│         provision_golden_image · start_human_session (NEW)     │
│  human sessions: enable Screen Sharing (ssh kickstart),        │
│                  open vnc://, background disconnect monitor     │
└───────────────────────────────────────────────────────────────┘
        ▲ MCP tool calls                    │ lease handle / vnc url
        │                                    ▼
  Skill `mac-vm-test` (per agent session):
   AGENTIC:  acquire_vm → host-build → install+launch → drive via
             tart input/accessibility → assert → release_vm
   HUMAN:    acquire_vm → host-build → start_human_session(app) →
             [human drives via Screen Sharing] → auto release_vm
```

## Component changes

### 1. `Config` (config.py) — additions

```
human_session_grace_seconds   = 30     # no-connection window before teardown
human_session_connect_timeout = 600    # wait for the human's first connect
human_session_ttl             = 14400  # 4h; lease TTL while a human session is active
vnc_port                      = 5900   # guest Screen Sharing / VNC port
```

`vnc_password` is **generated per session** (throwaway), not configured.

**Lease TTL vs. human sessions:** the default `lease_ttl` (1800s) would let `reap`
destroy a VM mid-manual-test. So `start_human_session` extends the lease's
`expires_at` by `human_session_ttl`, and the monitor **refreshes `expires_at` on
every successful probe** (a keep-alive while connected). An actively-connected
session is therefore never reaped; teardown comes only from disconnect+grace,
`connect_timeout`, or explicit `release_vm`.

### 2. `Host` protocol (host.py) + `LocalHost` (local_host.py)

- **`wait_agent(name, timeout)`** — poll `tart exec <name> true` until it exits 0
  or the timeout elapses (raises `TimeoutError`). `FakeHost` returns immediately.
- **`boot(name, graphics=False)`** — add a `graphics` flag; `LocalHost` appends
  `--no-graphics` when `graphics=False` (the new default). `FakeHost` ignores it.
  Rationale: the pool never needs the built-in window; a headless boot avoids a
  stray tart window competing with Screen Sharing and saves host resources.

### 3. `LeasePool.acquire` (pool.py)

Insert `self.host.wait_agent(name, timeout=self.cfg.acquire_wait_timeout)` after
`wait_ip`. Lease is only returned once the guest agent's RPC actually answers.
This makes the pool service behave like the battle-tested `vm-acquire.sh`.

### 4. New module `human_session.py`

Single-responsibility helpers, all with injectable `runner` / `clock` / `probe`
so they unit-test against `FakeHost` without a Mac (mirrors `baker.py`'s
`runner=` injection):

- **`deploy_app(ip, ssh_key, app_path, runner)`** — `scp -i key` the built `.app`
  into `~/Apps` in the guest.
- **`launch_app(vm_name, app_basename, tart_bin, runner)`** — `tart exec <vm>
  open ~/Apps/<App>.app`.
- **`enable_screen_sharing(ip, ssh_key, runner) -> vnc_password`** — over SSH,
  generate a random 8-char VNC password and run
  `sudo .../ARDAgent.app/Contents/Resources/kickstart -activate -configure
  -access -on -restart -agent -privs -all -clientopts -setvnclegacy -vnclegacy
  yes -setvncpw -vncpw <pw>` (sudo via `echo admin | sudo -S`, SIP is off in the
  base image). Returns the password.
- **`open_screen_sharing(ip, vnc_password, opener)`** — `open
  "vnc://:<pw>@<ip>"` on the host (launches Screen Sharing.app in the user's
  session). `opener` defaults to `subprocess.run` but is injectable.
- **`HumanSessionMonitor`** — a `threading.Thread` implementing the teardown
  state machine (below), constructed with a `probe()` callable (returns True when
  a `:5900` connection is ESTABLISHED), a `clock`, `grace_seconds`,
  `connect_timeout`, and an `on_close()` callback.

### 5. `PoolService` (server.py) — new tool

```python
def start_human_session(self, lease_id, app_path=None, bundle_id=None) -> dict:
    lease = self.pool.status(lease_id)          # error if not found
    if app_path:
        deploy_app(lease.ip, key, app_path, ...)
        launch_app(lease.vm_name, basename(app_path), tart, ...)
    pw = enable_screen_sharing(lease.ip, key, ...)
    open_screen_sharing(lease.ip, pw, ...)
    self._start_monitor(lease, on_close=lambda: self.release_vm(lease_id))
    return {"vnc_url": f"vnc://:{pw}@{lease.ip}", "vm_name": lease.vm_name,
            "ip": lease.ip, "monitoring": True}
```

Registered in `main()` alongside the existing tools:
`mcp.tool()(svc.start_human_session)`.

### 6. Disconnect monitor state machine

Probe = SSH into the guest and check for an ESTABLISHED connection on `:5900`
(`netstat -an -p tcp | grep '\.5900 .*ESTABLISHED'`; `lsof -iTCP:5900
-sTCP:ESTABLISHED` is an equivalent fallback).

1. **Await connect** — poll every ~3s until the probe is true (human connected),
   or `connect_timeout` elapses → release with reason `never_connected` (frees
   the slot if the human walks away).
2. **Await disconnect** — poll; on each true probe, **refresh the lease's
   `expires_at`** (keep-alive, so `reap` never kills an active session). When the
   probe has been false continuously for `grace_seconds`, call `on_close()` →
   `release_vm`. A reconnect during the grace window **resets** the timer (a
   transient network blip does not nuke the VM). This is the "auto-destroy on
   window close" semantics.

**Manual escape hatch:** because the flow is MCP-driven (no foreground terminal),
the human tells Claude to tear down now and the agent calls `release_vm(lease_id)`
directly; `vm_status` / `list_pool` remain available to observe the session.

### 7. Skill `mac-vm-test` rewrite

SKILL.md is restructured around the MCP tools:

- **Prerequisites** — the `mac-vm-pool` MCP server is registered and running
  (replaces the "point `MVP_TART_BIN` and run the bash scripts" instructions).
- **Agentic flow** — `acquire_vm` → host build → install+launch → drive via
  `tart input` / `tart accessibility` → assert → `release_vm` (in a failure-safe
  wrapper that always releases). Here install+launch stay **direct** host-side
  `scp` / `tart exec` calls (same layer as driving), consistent with the
  non-goal above — only the human path bundles install+launch into
  `start_human_session` (because the service then opens Screen Sharing as one
  atomic handoff).
- **Human flow (new section)** — `acquire_vm` → host build →
  `start_human_session(lease_id, app_path)` → tell the user the app is up in
  Screen Sharing and they can drive it → the VM auto-releases on close (or the
  user asks Claude to release now).
- `vm-acquire.sh` / `vm-release.sh` are **retired** (removed; their logic now
  lives — correctly, with the RPC-readiness wait — in the pool service).

### 8. MCP registration

Keep `transport="streamable-http"` (the 2026-07-01 design requires a shared
daemon so parallel agents can't collectively exceed the 2-VM cap; stdio would
give each session its own daemon and break that invariant). Deliverables:

- A documented run command (`python -m mac_vm_pool.server`) and a launchd/
  background recipe so the daemon stays up.
- The Claude Code MCP registration entry (URL of the streamable-http server) in
  `~/.claude.json` (or a project `.mcp.json`), plus README instructions.

## Data flow — human session

```
agent: xcodebuild → APP=/tmp/dd/.../MyApp.app
agent → MCP acquire_vm(client_id)                    → {lease_id, vm_name, ip}
agent → MCP start_human_session(lease_id, APP)
          service: scp APP → guest ~/Apps
          service: tart exec open ~/Apps/MyApp.app
          service: ssh kickstart (+ vnc pw)          → pw
          service: open vnc://:pw@ip                 (Screen Sharing opens)
          service: spawn HumanSessionMonitor(...)
        ← {vnc_url, vm_name, ip, monitoring:true}
[human drives the app via Screen Sharing; agent may still screenshot/AX on request]
human closes Screen Sharing → probe false for grace_seconds
          monitor → on_close → MCP release_vm(lease_id) → tart stop+delete
```

## Error handling & safety

- **Fail fast on acquire** — cap reached (`capacity()<=0`) returns the existing
  `{queued, reason:"cap_reached"}`; RPC never comes up raises `TimeoutError`.
  `start_human_session` is never reached without a live lease.
- **No VM leaks** — teardown paths: monitor `on_close`, `connect_timeout`,
  explicit `release_vm`, lease TTL `reap`, and `reconcile` on daemon startup
  (destroys orphan `pool-*` VMs). Any one of these reclaims a VM.
- **Grace + reconnect reset** — transient Screen Sharing drops don't destroy the
  session.
- **Partial-failure in `start_human_session`** — setup (`deploy_app`/`launch_app`/
  `launch_bundle`/`enable_screen_sharing`/`open_screen_sharing`) runs inside a
  `try/except`. On any failure the lease is **released** and the error re-raised —
  because no reaper is wired to reclaim it, leaving the lease would leak a scarce
  VM slot with no monitor to free it. The caller re-acquires and retries. The
  monitor is only created after setup succeeds.
- **Monitor lifecycle** — `release_vm` stops (`monitor.stop()`) and forgets the
  session monitor, so an explicit release doesn't leave an orphaned thread
  SSHing into a deleted guest; the monitor also removes itself on auto-close. The
  `_monitors` registry is guarded by a lock (FastMCP serves requests on multiple
  threads).
- **App transfer** — the `.app` is streamed with `tar` over SSH (not `scp -r`,
  which dereferences the bundle's internal symlinks and breaks its code
  signature), and the guest target is cleared first so a re-deploy is idempotent.

## Security considerations

- **Throwaway per-lease VNC password**, set only when a human session starts and
  destroyed with the VM. Screen Sharing is **off during agentic runs** (enabled
  per-session), keeping that path's surface unchanged.
- Screen Sharing listens on the guest's Virtualization **NAT/shared IP**, reached
  from the host only — not exposed externally.
- `admin`/`admin` + SIP-off are inherent to the disposable cirruslabs base image
  and acceptable for local, single-use VMs (unchanged from today).
- The daemon runs locally under the user; the MCP server is bound to localhost.

## Testing strategy

**Unit (no Mac; `pytest -m "not integration"`)** — using `FakeHost` and injected
`runner`/`clock`/`probe`:

- `LocalHost.wait_agent` success and timeout paths (canned `runner`).
- `LeasePool.acquire` now calls `wait_agent` (assert order via a spy host).
- `enable_screen_sharing` composes the correct `kickstart` command and returns a
  password (assert on captured args).
- `HumanSessionMonitor` state machine: never-connects → `never_connected`;
  connect → disconnect → grace → `on_close` fires once; reconnect during grace
  cancels teardown; uses a fake clock and a scripted probe sequence.
- `start_human_session` orchestration with all helpers mocked (deploy → launch →
  enable → open → monitor started; error short-circuits before monitor).

**Integration (`MVP_RUN_INTEGRATION=1`, real Mac + baked image)** — a manual
checklist: acquire → `start_human_session` with a sample `.app` → confirm Screen
Sharing opens and the app is frontmost → drive it by hand → close the window →
assert the VM auto-releases within grace + poll interval.

## Implementation phasing

### Phase 1 — parity (pool service replaces the skill's bash lifecycle)
1. Add `wait_agent` to `Host`/`LocalHost`/`FakeHost`; call it in
   `LeasePool.acquire`. Unit tests.
2. Add `graphics=False` default to `boot` (`--no-graphics`). Unit test.
3. Register the `mac-vm-pool` MCP server in Claude Code; document running it
   (README + config snippet).
4. Rewrite SKILL.md's agentic flow to use the MCP tools; delete `vm-acquire.sh` /
   `vm-release.sh`.
5. Verify end-to-end: an agentic macOS-app test runs entirely through the pool
   service.

### Phase 2 — human session
6. Add `human_session.py` (`deploy_app`, `launch_app`, `enable_screen_sharing`,
   `open_screen_sharing`, `HumanSessionMonitor`). Unit tests.
7. Add `Config` fields; add `start_human_session` to `PoolService`; register the
   MCP tool.
8. Add the "Human / manual testing" section to SKILL.md.
9. Integration checklist pass on a real Mac.

## Open questions / risks

- **`open vnc://` auth UX** — Screen Sharing may still prompt on some macOS
  versions even with a URL-embedded VNC legacy password. Mitigation: fall back to
  account-based `vnc://admin@ip` (admin/admin) if legacy VNC auth misbehaves;
  finalize during Phase 2 integration.
- **Daemon lifetime vs monitor** — the monitor thread lives in the daemon; if the
  daemon is killed mid-session the VM is reclaimed later by `reconcile`/TTL rather
  than instantly. Acceptable; documented.
- **`kickstart` path/flags across macOS versions** — pinned to the cirruslabs
  Sequoia base; re-verify if the base image bumps.
- **`acquire` holds the pool lock across `wait_ip` + `wait_agent`** (deferred,
  low severity) — a slow boot can hold the `RLock` for up to ~2×
  `acquire_wait_timeout`, blocking `status`/`release`/`extend`/`list_pool`. This
  is a pre-existing pattern (`wait_ip` was already under the lock); the safe fix
  (reserve the slot under the lock, do the blocking boot outside it, finalize
  under the lock) is a non-trivial refactor of the cap-enforcement path and is
  left for a follow-up rather than risked here.
- **No graceful monitor join on server shutdown** (low) — monitors are daemon
  threads and `reconcile()` cleans orphans on startup, so a mid-flight teardown
  interrupted by process exit is recovered next start rather than instantly.
