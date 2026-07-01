# mac-vm-pool — Design Spec

**Date:** 2026-07-01
**Status:** Approved design, pending implementation plan

## Purpose

Give parallel Claude Code agents a systematic workflow to **compile macOS apps
on the host and run functional tests inside isolated macOS VMs**, driving the
app through the input-injection and accessibility RPCs we added to the Tart
guest agent. A pool manager leases ready-to-use VMs to agents, enforcing the
hard limit that Apple's Virtualization.framework imposes.

## The dominant constraint

Apple's Virtualization.framework allows **at most 2 running macOS guest VMs per
physical Mac** (framework + macOS SLA). Each macOS VM also wants ~4–8 GB RAM and
a couple of cores. Real parallelism for macOS UI testing therefore requires a
fleet of Macs; on a single host, concurrency is capped at 2. The pool manager
treats this cap as a first-class invariant and queues work beyond it.

## Scope (agreed)

| Axis | Decision |
|------|----------|
| Topology | Single Mac now; allocation API designed to extend to a fleet without rewrite |
| Clients | Parallel Claude Code agents, short-lived per-task leases |
| Test model | Drive via our `tart input` / `tart accessibility` RPCs; assert on AX tree state and/or screenshots |
| Build location | Compile on the **host** (fast, shared caches); install the built `.app` into the leased VM |
| Reset policy | **Fresh CoW clone per lease** from a golden image; destroy on release |
| Golden image | **Baked by tooling** (`provision_golden_image`), re-baked only when the guest agent changes |

## Non-goals

- Compiling inside the VM (no Xcode baked into images).
- XCUITest/XCTest integration (may come later; not this spec).
- Multi-host fleet orchestration *implementation* (only the abstraction is built now).
- Managing Linux guests (workflow is macOS-app testing).

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  vm-pool daemon (Python, long-running)                       │
│  • golden image ref   • lease table   • 2-VM cap + wait queue│
│  • warm pool (pre-booted VMs)   • Host abstraction (fleet)   │
│  • background loop: prewarm / clone-on-acquire / destroy     │
│                     ▲ in-process                             │
│  MCP server (HTTP)  ┘  ← all parallel agents share ONE daemon│
│  tools: acquire_vm · release_vm · vm_status · list_pool ·    │
│         provision_golden_image                               │
└─────────────────────────────────────────────────────────────┘
        ▲ acquire/release                 │ returns handle
        │                                  ▼
  Skill `mac-vm-test` (per agent):
  acquire → host-build .app → install in VM → drive input/accessibility
  → screenshot/assert → release
```

Three units with distinct responsibilities:

1. **`vm-pool` daemon** — single source of truth for VM lifecycle and leases.
2. **MCP server** — thin lease API over HTTP, hosted in the daemon process.
3. **Skill `mac-vm-test`** — the per-agent test playbook that consumes a lease.

HTTP transport is required: parallel agents run in separate sessions and must
share one daemon, or the 2-VM cap cannot be enforced.

## Component 1 — vm-pool daemon

### Responsibilities
- Own the golden image reference and the lease table.
- Enforce `max_vms_per_host` (default 2).
- Maintain a warm pool of pre-booted VMs (default `warm_target = 1`).
- Clone-on-acquire when no warm VM is available and capacity remains.
- Destroy-on-release and refill the warm pool.
- Reap expired leases (dead agents) so VMs never leak.
- Reconcile on startup: compare `tart list` running VMs against the lease
  table and destroy orphans.

### Lease record
```
lease_id        uuid
vm_name         tart VM name, e.g. "pool-<short>"
host_id         "local" now; host key when fleet-enabled
client_id       agent identifier
state           warming | ready | leased | releasing | dead
ip              guest IP
control_socket  path to the tart control socket
created_at, expires_at   lease TTL (default 1800s), renewable
```

### Warm pool
VMs in `ready` state: cloned and booted, agent up, not yet leased. Keeping one
warm makes the common acquire path instant; the second slot clones on demand.

### Config (file + env)
`max_vms_per_host=2`, `warm_target=1`, `golden_image="mac-test-golden"`,
`base_image="ghcr.io/cirruslabs/macos-sequoia-base:latest"`,
`lease_ttl=1800`, `acquire_wait_timeout=300`, `ssh_key_path`.

## Component 2 — MCP server (tools)

- **`provision_golden_image(base_image, agent_binary_path, name)`** → bakes the
  golden image (see below). Returns image name + smoke-test result.
- **`acquire_vm(client_id, ttl_seconds=1800, wait_timeout=300)`** →
  `{lease_id, vm_name, ip, ssh_key_path, control_socket, expires_at}`; or a
  `queued` status with position if it times out waiting for a slot.
- **`release_vm(lease_id)`** → destroys the VM, refills warm pool, wakes next
  waiter.
- **`vm_status(lease_id)`** → state, ip, expires_at, remaining seconds.
- **`list_pool()`** → all VMs/leases, warm count, cap usage, queue depth.
- **`renew_lease(lease_id, ttl_seconds)`** → extends expiry (heartbeat).

## Component 3 — Skill `mac-vm-test`

The playbook an agent follows, using MCP tools plus the built `tart` CLI:

1. `acquire_vm` → handle.
2. **Host build**: `xcodebuild -scheme … -derivedDataPath … build` (or
   `swift build`); locate the `.app`.
3. **Install**: copy the `.app` into the VM over the baked SSH key
   (`scp`/`rsync`), or `tart exec` to place it.
4. **Launch**: `tart exec <vm> open /path/App.app`.
5. **Drive**: `tart input …` / `tart accessibility …` against the leased VM.
6. **Observe**: `screencapture` in guest → pull image to host.
7. **Assert**: AX-tree checks (`accessibility find/act`) and/or screenshot diff.
8. **On failure**: auto-capture a screenshot + `accessibility dump` for
   debugging.
9. `release_vm`.

The skill documents the gotchas learned during the prototype: the Accessibility
TCC grant is mandatory (CGEventPost and AX APIs are filtered without it), and
input requires a booted GUI session.

## Golden image baking

`provision_golden_image` automates the manual sequence proven in the prototype:

1. Clone base image.
2. Boot; wait for SSH.
3. `scp` the modified `tart-guest-agent`; swap the binary; reload the launchd
   agent.
4. Write TCC grants (`kTCCServiceAccessibility`, `kTCCServiceScreenCapture`,
   `kTCCServicePostEvent`) — SIP is off in cirruslabs base images.
5. Install a generated SSH public key into `authorized_keys` (removes the
   sshpass fragility hit in the prototype).
6. Smoke test: type text + query the accessibility tree; verify both work.
7. Clean shutdown.
8. Commit as local image `mac-test-golden`.

Re-bake only when the guest agent binary changes.

## Failure & recovery

- **Lease TTL + reaper**: expired leases are reclaimed; dead agents don't leak
  VMs.
- **Release always destroys**: fresh-clone model means no dirty-state bleed
  between agents.
- **Daemon crash recovery**: reconcile `tart list` vs lease table on startup;
  destroy orphans.
- **Cap safety**: never exceed 2 running VMs; excess acquires queue.

## Fleet-readiness (abstraction only)

Allocation targets a `Host` interface: `capacity()`, `clone()`, `boot()`,
`delete()`, `list()`. Today only `LocalHost` (shells to `tart`). Adding Macs
later means implementing `RemoteHost` (SSH or per-host agent) and registering
them; the MCP API and skill are unchanged. The allocator picks any host with
free capacity.

## Tech stack & prerequisites

- **Python** (consistent with the existing `benchling-mcp`), MCP SDK over HTTP.
- Shells out to `tart` and the built `tart` CLI (with `input`/`accessibility`),
  plus `ssh`/`scp` using the baked key.
- **Prerequisites** (from the prototype, in `~/repos`):
  - modified `tart-guest-agent` binary (input + accessibility RPCs),
  - `tart` CLI built with the `input` / `accessibility` commands.
- Home: `~/repos/mac-vm-pool/` — daemon, MCP server, and the skill.

## Decisions locked in

- SSH key baked into the golden image (not admin/admin passwords).
- `warm_target = 1` (one VM pre-booted; second slot clones on demand).

## Suggested build milestones

1. **Golden image baker** — `provision_golden_image` end to end; validate the
   smoke test. (Unblocks everything.)
2. **Daemon core** — lease table, cap enforcement, clone/boot/destroy against
   `LocalHost`; no warm pool yet.
3. **MCP facade** — acquire/release/status/list over HTTP; single-agent happy
   path.
4. **Warm pool + queue** — pre-warming, wait queue, TTL reaper, crash
   reconciliation.
5. **Skill `mac-vm-test`** — the full build→install→drive→assert→release
   playbook, including failure capture.
6. **Fleet abstraction** — extract the `Host` interface (LocalHost only) so
   remote hosts can be added later.
