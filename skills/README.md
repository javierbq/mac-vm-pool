# Skills

Claude Code skills that ship with `mac-vm-pool`. This directory is the **source
of truth**; Claude Code loads skills from `~/.claude/skills/`, so deploy by
symlinking (recommended — edits stay in sync) or copying.

## `mac-vm-test`

Host-compile a macOS app and functionally test it inside a fresh, disposable VM
— either agent-driven (via the `tart` input/accessibility RPCs) or handed off to
a human over macOS Screen Sharing. All VM lifecycle goes through this repo's
`mac-vm-pool` MCP server (`acquire_vm` / `release_vm` / `start_human_session`),
so that server must be running (see the top-level README).

### Deploy

```sh
# symlink (recommended): edits in the repo are picked up immediately
ln -sfn "$PWD/skills/mac-vm-test" ~/.claude/skills/mac-vm-test

# or copy
mkdir -p ~/.claude/skills && cp -R skills/mac-vm-test ~/.claude/skills/
```
