# Laptop BBG-host setup — step-by-step

> **Where this doc lives (three copies, in case one's unreachable):**
> - `C:\Users\ndiaz\Dropbox\RCG_2020\laptop_onboarding\LAPTOP_SETUP.md` — canonical copy for the laptop (Dropbox syncs it once you sign in)
> - `/home/nixos/Prod/V1/docs/LAPTOP_SETUP.md` — on rcg-nixos. From anywhere with Tailscale: `ssh nixos@rcg-nixos cat /home/nixos/Prod/V1/docs/LAPTOP_SETUP.md`
> - `http://rcg-nixos:8080/LAPTOP_SETUP.md` — browser, via Tailscale
>
> The two patched scripts (`bloomberg_prices.py`, `rcg_heartbeat.py`) are also pre-staged in `Dropbox/RCG_2020/laptop_onboarding/` — §4 just copies them to `C:\Users\ndiaz\Downloads\` on the laptop.

**When to use this:** You have a second Windows machine (the travel laptop, or any new office desktop) that you want to be able to log into Bloomberg from. After this setup, the BBG pull "follows" wherever you log in — both boxes try every fire, whichever has the active BBG session writes the file.

**Time estimate:** ~45 min the first time. ~15 min for each subsequent box.

**Prerequisites:**
- The box is on your Tailscale tailnet (`tailscale status` from rcg-nixos shows it, even if offline)
- You have a working RDP / physical session on the box
- BBG account credentials handy (Bloomberg may force re-auth on first login from a new device)

---

## 1. Install Bloomberg Terminal + log in

Standard Bloomberg installer. Log in with your B-Unit. **Note:** Logging in here will kick your active session on rcg-base — that's expected and reversible.

Verify BBG is alive: open a function window, type `HELP <GO>` — if you get the help screen, you're in.

## 2. Install Python + blpapi

```powershell
# Option A: if Anaconda is already installed
C:\Users\ndiaz\Anaconda3\python.exe -m pip install blpapi

# Option B: install Anaconda fresh (matches the rcg-base setup)
# Download from https://www.anaconda.com/download
# Then run Option A.
```

Verify:
```powershell
C:\Users\ndiaz\Anaconda3\python.exe -c "import blpapi; print(blpapi.__version__)"
```

## 3. Set up SSH key for SCP back to NixOS

The puller uses the OpenSSH client to SCP files to rcg-nixos. Needs a key without a passphrase (Task Scheduler can't type one).

```powershell
# Generate a key (skip if you already have ~/.ssh/id_ed25519)
ssh-keygen -t ed25519 -f $HOME\.ssh\id_ed25519 -N ""

# Print the public key — you'll paste this onto rcg-nixos in the next step
Get-Content $HOME\.ssh\id_ed25519.pub
```

Then on rcg-nixos (SSH in from any device):
```bash
# Append the laptop's public key
echo "<paste the ed25519 public key here>" >> ~/.ssh/authorized_keys
```

Verify from the laptop:
```powershell
ssh -o BatchMode=yes nixos@100.78.59.48 hostname
# Should print: nixos
```

If that fails, neither `bloomberg_prices.py` nor `rcg_heartbeat.py` will work.

## 4. Copy the two scripts to the laptop

Both patched scripts are already in your Dropbox — just copy them locally:

```powershell
$src = "C:\Users\ndiaz\Dropbox\RCG_2020\laptop_onboarding"
$dst = "C:\Users\ndiaz\Downloads"
Copy-Item "$src\bloomberg_prices.py" "$dst\bloomberg_prices.py" -Force
Copy-Item "$src\rcg_heartbeat.py"    "$dst\rcg_heartbeat.py"    -Force
```

If Dropbox hasn't finished syncing the `laptop_onboarding/` folder yet, fall back to scp from rcg-base:

```powershell
scp "ndiaz@100.86.90.78:C:/Users/ndiaz/Downloads/bloomberg_prices.py" "C:\Users\ndiaz\Downloads\"
scp "ndiaz@100.86.90.78:C:/Users/ndiaz/Downloads/rcg_heartbeat.py"    "C:\Users\ndiaz\Downloads\"
```

No edits to either script are needed — both self-detect the local hostname via `socket.gethostname()`.

## 5. Quick sanity-test each script manually

Before registering Task Scheduler:

```powershell
# Heartbeat — should write local file + SCP, exit 0
C:\Users\ndiaz\Anaconda3\python.exe C:\Users\ndiaz\Downloads\rcg_heartbeat.py

# BBG pull — depends on whether BBG is logged in HERE right now.
# If BBG is on the laptop: script pulls data, writes JSON, SCPs to NixOS.
# If BBG is on rcg-base:   script prints "no local BBG terminal session, skipping" and exits 0.
C:\Users\ndiaz\Anaconda3\python.exe C:\Users\ndiaz\Downloads\bloomberg_prices.py
```

Verify on rcg-nixos (`ssh nixos@rcg-nixos`):
```bash
ls -la /home/nixos/Prod/V1/var/heartbeat_*.txt
# You should now see a heartbeat_<LAPTOP-HOSTNAME>.txt alongside any existing one.
```

## 6. Register Task Scheduler entries

Same `schtasks` commands as we used on rcg-base — just run them from the laptop's PowerShell.

```powershell
# Heartbeat — every 10 min, 24/7
$action_hb = "`"C:\Users\ndiaz\Anaconda3\python.exe`" `"C:\Users\ndiaz\Downloads\rcg_heartbeat.py`""
schtasks /Create /SC MINUTE /MO 10 /TN "RCG Heartbeat" /TR $action_hb /F

# BBG pull — every 30 min, all hours
# (The scheduler will fire on every box; only the BBG-active one does work.)
$action_bbg = "`"C:\Users\ndiaz\Anaconda3\python.exe`" `"C:\Users\ndiaz\Downloads\bloomberg_prices.py`""
schtasks /Create /SC MINUTE /MO 30 /TN "RCG BBG Pull" /TR $action_bbg /F

# Trigger both once so they start clean
schtasks /Run /TN "RCG Heartbeat"
schtasks /Run /TN "RCG BBG Pull"
```

## 7. Tell rcg-nixos to expect this box online

Edit `/home/nixos/Prod/V1/var/active_peers.conf` and uncomment the laptop's line (or add a new one if it's a different box). Find the Tailscale IP via `tailscale status | grep <hostname>`.

```bash
ssh nixos@rcg-nixos
cd /home/nixos/Prod/V1/var/
# Edit active_peers.conf — example:
#   rcg-base:100.86.90.78
#   rcg-laptop:100.87.212.98     ← uncomment / add this line
```

No restart needed; the probe reads this file on every fire.

## 8. End-to-end smoke test

On rcg-nixos:

```bash
# Force the probe to run once
systemctl --user start rcg-infra-probe.service
sleep 3

# Should see TWO heartbeat files, both fresh
ls -la /home/nixos/Prod/V1/var/heartbeat_*.txt

# Log file should have no new STALE / FAIL / MISSING entries from this run
tail /home/nixos/rcg_infra_probe.log

# The aggregate "freshest" heartbeat is whichever box just wrote
```

## 9. Test BBG-host failover

The real test of the multi-host setup:

1. With BBG logged in on rcg-base, watch `bloomberg_prices.json` mtime tick up every 30 min — rcg-base is doing the work.
2. Log into BBG on the laptop (kicks rcg-base's session).
3. Wait for the next Task Scheduler fire (≤30 min).
4. `bloomberg_prices.json` mtime should still be ticking — now the laptop is doing the work. Verify by tailing the Windows-side script logs on each box.

If you want to confirm WHICH box wrote the file, the SCP atomic-mv leaves no host signature in the destination file itself, but the local `Dropbox\RCG_2020\bloomberg_prices.json` on each box will be fresh only on the box that succeeded.

---

## Removing a box from rotation later

If a box goes into permanent storage:

1. On Windows: `schtasks /Delete /TN "RCG Heartbeat" /F` and the BBG one (or just power off — they'll skip silently).
2. On rcg-nixos: comment out (or delete) the box's line in `active_peers.conf`.

The probe will silently stop monitoring that peer. The old `heartbeat_<HOSTNAME>.txt` file stays in `var/` (harmless — `ls` shows it as old). You can `rm` it to declutter.
