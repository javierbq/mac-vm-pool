---
name: mac-vm-test
description: Compile a macOS app on this Mac and functionally test it inside an isolated, disposable macOS VM — either agent-driven (clicks, typing, menu actions, assert on screenshots/accessibility tree) or handed off to a human for manual testing via macOS Screen Sharing. Use when the task is to build a macOS/Swift app and verify its UI behavior without touching the host's own UI. All VM lifecycle goes through the mac-vm-pool MCP server; the agent drives via the tart input/accessibility RPCs. Requires the mac-vm-pool MCP server running + a baked mac-test-golden image + the entitled tart CLI (see mac-vm-pool README).
---

# mac-vm-test

Host-compile a macOS app, install it into a **fresh, isolated macOS VM**, then
either **drive it automatically** (agentic testing) or **hand it to a human**
for manual testing over Screen Sharing. The VM is disposable (destroyed on
release), so tests never pollute the host and never bleed between runs.

VM lifecycle (acquire / release / status / human session) goes through the
**`mac-vm-pool` MCP server**. Low-level driving (clicks, typing, accessibility)
uses the entitled `tart` CLI directly.

## Prerequisites (fail fast if missing)

- The **`mac-vm-pool` MCP server is running and registered** in Claude Code, so
  the `mcp__mac-vm-pool__*` tools are available. If they aren't, start it and
  register it (see `~/repos/mac-vm-pool` README, "Running the pool service"):
  ```bash
  # start (or install the LaunchAgent), then confirm it connects:
  claude mcp list | grep mac-vm-pool
  ```
- A baked golden image named `mac-test-golden` (the pool's `provision_golden_image`
  tool bakes it; re-bake only when the guest agent changes).
- The `tart` CLI built with `input`/`accessibility` **and codesigned with the
  virtualization entitlement**, for driving. Set `MVP_TART_BIN`, e.g.
  `~/repos/tart/.build/debug/tart`.
- The baked SSH key at `~/.mac-vm-pool/id_ed25519` (for installing the `.app`).

## Agentic flow — do these in order

Skill dir is referenced as `$SK` (`~/.claude/skills/mac-vm-test`).

### 1. Acquire a VM
Call the MCP tool `mcp__mac-vm-pool__acquire_vm` with a `client_id` (any string
identifying this run). It returns a handle:
```
{ "lease_id": "...", "vm_name": "pool-...", "ip": "10.0.0.x", ... }
```
The pool boots a fresh clone headless and blocks until the guest-agent RPC is
actually ready before returning. If it returns `{"queued": true, "reason":
"cap_reached"}`, the 2-VM cap is full — release a VM or stop and report.

### 2. Build the app ON THE HOST
```bash
xcodebuild -project MyApp.xcodeproj -scheme MyApp -derivedDataPath /tmp/dd -destination 'platform=macOS' build
APP=$(/usr/bin/find /tmp/dd/Build/Products -maxdepth 2 -name '*.app' | head -1)
```
(Or `swift build` for SwiftPM apps.) Building on the host is faster and keeps the
VM lean.

### 3. Install the .app into the VM (over the baked key)
```bash
KEY=~/.mac-vm-pool/id_ed25519
ssh -i "$KEY" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null admin@"$IP" 'mkdir -p ~/Apps'
scp -i "$KEY" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -r "$APP" admin@"$IP":~/Apps/
```
(`$IP` from the acquire handle.)

### 4. Launch it in the guest
```bash
"$MVP_TART_BIN" exec "$VM" open /Users/admin/Apps/"$(basename "$APP")"
sleep 3   # let it come to the foreground
```
(`$VM` is the `vm_name` from the handle.)

### 5. Drive the UI — prefer accessibility over coordinates
Coordinate clicks are brittle; query the accessibility tree and act on elements
by role/title/identifier when you can.
```bash
"$MVP_TART_BIN" accessibility find "$VM" --app com.example.MyApp --role AXButton
"$MVP_TART_BIN" accessibility act  "$VM" --app com.example.MyApp --title "Sign In" --action press
"$MVP_TART_BIN" input type  "$VM" "hello@example.com"
"$MVP_TART_BIN" input key   "$VM" "cmd+s"
"$MVP_TART_BIN" input click "$VM" 640 480
```

### 6. Observe & assert
Capture the screen through the **guest agent** (`tart exec`), NOT over SSH. The
baker grants Screen Recording (`kTCCServiceScreenCapture`) to the guest-agent
binary, so a capture whose responsible process is the agent runs silently. A
capture invoked over SSH is attributed to `com.apple.sshd-session`, which has no
grant, so on macOS 15+/26 it raises an un-dismissable "bypass the private window
picker" consent overlay that then contaminates the screenshot.
```bash
"$MVP_TART_BIN" exec "$VM" screencapture -x /tmp/shot.png   # runs as the granted agent → no prompt
scp -i "$KEY" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null admin@"$IP":/tmp/shot.png /tmp/shot.png
```
Assert on the accessibility tree (`accessibility find` returns element state,
values, frames) and/or inspect the screenshot. `accessibility find` exits
non-zero when nothing matches — usable directly as a boolean assertion.

### 7. ALWAYS release — even on failure
Release the VM via `mcp__mac-vm-pool__release_vm` with the `lease_id`, no matter
what. On failure, before releasing, capture diagnostics:
```bash
"$MVP_TART_BIN" accessibility find "$VM" --app com.example.MyApp --max-results 100 > /tmp/ax-dump.txt 2>&1 || true
"$MVP_TART_BIN" exec "$VM" screencapture -x /tmp/fail.png || true   # via the agent (see §6), not ssh
```

## Manual / human testing flow

Use this when the human wants to poke at the app themselves with mouse and
keyboard inside the sandbox, instead of the agent driving. It uses **tart's
built-in window** — the hypervisor renders a native window on this Mac and
injects real mouse/keyboard. No guest Screen Sharing / VNC / auth is involved.

### 1. Acquire in window mode
Call `mcp__mac-vm-pool__acquire_vm` with `client_id` **and `display="window"`**.
This boots the VM with tart's built-in UI, so a **native VM window appears on
this Mac** as it comes up. (Omit `display` for the headless agentic flow above.)

### 2. Build on the host + hand off
Host-build the app (step 2 above → `$APP`), then call
`mcp__mac-vm-pool__start_human_session` with:
- `lease_id` — from the acquire handle,
- `app_path` — host path to the built `.app` (installed + launched in the VM), **or**
- `bundle_id` — to launch an already-installed app (e.g. `com.apple.TextEdit`).

The app then appears in the VM window. Returns
`{ "vm_name", "ip", "window": true, "monitoring": true, "teardown": … }`. Tell
the human the app is up in the VM window and they can click/type directly;
clipboard is shared via the guest agent.

### 3. Teardown
**Closing the VM window** stops the VM, and the backstop monitor releases the
lease automatically. Or call `mcp__mac-vm-pool__release_vm` with the `lease_id`
to tear down immediately. While the window is open the lease is kept alive so it
is never reaped mid-session. A failed hand-off does **not** destroy the VM — it's
kept so you can SSH in and inspect or retry.

## Cap & parallelism
Apple's Virtualization.framework allows **at most 2 running macOS VMs per Mac**.
The pool enforces this cap across all agents (it holds the lease table). If
`acquire_vm` returns `{"queued": true}`, release a VM or wait. More parallelism
needs more Macs (the pool's Host abstraction is built for that; remote hosts are
future work).

## If the agent RPC won't come up
`acquire_vm` already waits for guest-agent RPC readiness before returning. If it
times out, the golden image's guest agent isn't auto-starting on boot — re-bake
the image (`provision_golden_image`) or check the agent's launchd job inside a
clone via SSH.

> **Note:** VM lifecycle (acquire/release, the 2-VM cap, the RPC-readiness wait)
> lives in the `mac-vm-pool` service and is driven via its MCP tools — the skill
> no longer ships its own acquire/release scripts.
