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
