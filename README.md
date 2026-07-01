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
