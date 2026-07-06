# mac-vm-pool

Leases isolated macOS VMs to parallel Claude Code agents for host-compiled,
VM-tested macOS app functional testing (via the tart input/accessibility RPCs).

## Requirements
- Apple Silicon Mac; `tart` installed (`brew install cirruslabs/cli/tart`); `sshpass`.
- The modified `tart-guest-agent` binary (input + accessibility RPCs) and the
  `tart` CLI built with the `input`/`accessibility` commands (see
  `~/repos/tart-guest-agent`, `~/repos/tart`).
- **The built `tart` CLI must be codesigned with the virtualization
  entitlement**, or `tart run` fails with "The process doesn't have the
  com.apple.security.virtualization entitlement." A `swift build` binary is
  unsigned; sign it once (re-sign after every rebuild):

  ```sh
  codesign --sign - --entitlements ~/repos/tart/Resources/tart-dev.entitlements \
    --force ~/repos/tart/.build/debug/tart
  ```

  This one binary then does both VM lifecycle (`clone`/`run`/`ip`/`delete`) and
  the `input`/`accessibility` RPCs, so point `tart_bin` at it.

## Setup
1. `pip install -e '.[dev]'`
2. Generate the SSH key: `ssh-keygen -t ed25519 -f ~/.mac-vm-pool/id_ed25519 -N ""`
3. Point config at your binaries (defaults live in `config.py`; override via env):
   ```sh
   export MVP_TART_BIN=~/repos/tart/.build/debug/tart        # the entitled, input/accessibility-capable build
   export MVP_GUEST_AGENT_BINARY=/path/to/tart-guest-agent    # arm64, with the new RPCs
   ```
4. Bake the golden image: `python -c "from mac_vm_pool.config import Config; from mac_vm_pool.baker import bake_golden_image; print(bake_golden_image(Config.load(None)))"`
5. Run the server: `python -m mac_vm_pool.server`

## Running the pool service (MCP)

The pool is a **long-running MCP server** (FastMCP, streamable-http) that holds
the lease table in memory and enforces the 2-VM cap across all agents. The
`mac-vm-test` skill drives VM lifecycle **through this server** — it must be
running.

- Foreground (dev):
  ```sh
  MVP_TART_BIN=~/repos/tart/.build/debug/tart python -m mac_vm_pool.server
  # → Uvicorn running on http://127.0.0.1:8000  (MCP endpoint: /mcp)
  ```
- Keep it running (LaunchAgent): edit the paths in
  `deploy/com.accipiter.mac-vm-pool.plist`, then:
  ```sh
  cp deploy/com.accipiter.mac-vm-pool.plist ~/Library/LaunchAgents/
  launchctl load ~/Library/LaunchAgents/com.accipiter.mac-vm-pool.plist
  ```
- Register with Claude Code (once):
  ```sh
  claude mcp add --transport http --scope user mac-vm-pool http://127.0.0.1:8000/mcp
  claude mcp list        # mac-vm-pool should connect once the server is up
  ```

### MCP tools
- `acquire_vm(client_id, ttl_seconds=1800, display="headless")` → lease handle
  (`lease_id`, `vm_name`, `ip`, `display`) or `{queued, reason}` at cap.
  `display="window"` boots with tart's built-in UI window for human testing.
- `release_vm(lease_id)` → destroy the VM.
- `vm_status(lease_id)` / `list_pool()` → introspection.
- `provision_golden_image()` → (re)bake the golden image.
- `start_human_session(lease_id, app_path=None, bundle_id=None)` → **hands a
  window-mode VM to a human**: installs+launches `app_path` (or launches
  `bundle_id`) into the VM whose **built-in tart window** is already on screen.
  No guest Screen Sharing / VNC / auth — the hypervisor renders the window and
  injects real mouse/keyboard (needs no golden-image changes). The VM must have
  been acquired with `display="window"`. Teardown: **closing the window** stops
  the VM and the backstop monitor releases the lease; or call `release_vm`. A
  failed hand-off **keeps** the VM for inspection. The lease is kept alive while
  the window is open. Returns `{vm_name, ip, window, monitoring, teardown}`.
  Human-session knobs (env-overridable via `MVP_*`): `human_session_grace_seconds`
  (30), `human_session_connect_timeout` (600),
  `human_session_connect_confirmations` (2), `human_session_ttl` (14400).

## Test
- Unit: `pytest -m "not integration"`
- Integration (needs a Mac + a baked `mac-test-golden` image):
  `MVP_RUN_INTEGRATION=1 MVP_TART_BIN=... pytest -m integration`

## Validation status
The bake pipeline and the acquire→status→release pool lifecycle are validated
end-to-end against `macos-sequoia-base` (see the integration test). The golden
image is confirmed to carry the modified agent, all three TCC grants, and the
baked SSH key. Known Plan-2 item: the guest agent's launchd startup can be racy
on a freshly-cloned VM, so the eventual `mac-vm-test` skill must wait for
guest-agent RPC readiness (not just an IP) before driving input/accessibility.
