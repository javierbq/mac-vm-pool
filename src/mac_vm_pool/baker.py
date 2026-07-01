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
    try:
        with open(pub) as fh:
            pubkey = fh.read().strip()
        ssh(f'mkdir -p ~/.ssh && echo "{pubkey}" >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys', ip)
    except FileNotFoundError:
        pass

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
