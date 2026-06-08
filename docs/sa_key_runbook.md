# Service-Account Key — Runbook

**Purpose:** day-to-day operation of the long-lived JSON key for `rcg-prod-app@rcg-prod-12508.iam.gserviceaccount.com`, post the 2026-05-28 migration off impersonation.

---

## Where things live

| Item | Location |
|---|---|
| Key file (production) | `/etc/rcg/google_service_account.json` (chmod 0600, owner `nixos:users`) |
| Env vars (system-wide) | Set in `/etc/nixos/claude-finance.nix` → `environment.variables` |
| Setup script | `/home/nixos/Prod/V1/scripts/setup_sa_key.sh` |
| CCO approval doc | `docs/cco_sa_key_exemption_request.md` |

The key file is **only** on the NixOS production box. Not in Dropbox, not in GitHub, not in the rcg-infra repo (gitignored).

---

## One-time setup (already done — for reference)

The migration was performed via `scripts/setup_sa_key.sh` after MM approval 2026-05-28. The script handled:
1. Org-policy exemption for the specific SA
2. JSON key creation via `gcloud iam service-accounts keys create`
3. Install + permission lockdown
4. Probe upload to confirm

If the key is ever lost / wiped, re-run that script after a fresh `gcloud auth login`.

---

## Verifying the key is working

```bash
# As nixos user:
gcloud storage ls gs://rcg-prod-data/ | head -3
# Should succeed silently, no auth warnings.

# Probe write:
echo "probe" > /tmp/probe.txt
gcloud storage cp /tmp/probe.txt gs://rcg-prod-data/_probe/runbook_$(date +%s).txt
gcloud storage rm gs://rcg-prod-data/_probe/runbook_*.txt
```

If either fails with "Reauthentication failed" the env vars in nix config aren't being picked up — check `printenv GOOGLE_APPLICATION_CREDENTIALS` and confirm the file exists at that path.

---

## Quarterly rotation (every 90 days)

Add to calendar: rotate Q1 / Q2 / Q3 / Q4. Lightweight — about 2 minutes.

```bash
# 1. List existing keys (you should see one)
gcloud iam service-accounts keys list \
    --iam-account=rcg-prod-app@rcg-prod-12508.iam.gserviceaccount.com

# 2. Note the existing KEY_ID (looks like "abc123...")

# 3. Run setup script — it'll create a new key + install it
bash /home/nixos/Prod/V1/scripts/setup_sa_key.sh

# 4. Verify the new key works (see "Verifying" above)

# 5. Delete the OLD key
gcloud iam service-accounts keys delete <OLD_KEY_ID> \
    --iam-account=rcg-prod-app@rcg-prod-12508.iam.gserviceaccount.com

# 6. Log rotation to decision_log/YYYY-MM-DD/sa-key-rotation/
```

Rotation is non-disruptive — both old and new keys are valid until step 5. Services pick up the new key on their next invocation.

---

## Emergency revocation

If the key is suspected compromised (e.g., NixOS box stolen, machine sold, suspicious GCS activity):

```bash
# From any authorized workstation:
gcloud auth login    # use nick.diaz@robincapitalgroup.com
gcloud iam service-accounts keys list \
    --iam-account=rcg-prod-app@rcg-prod-12508.iam.gserviceaccount.com

# Delete ALL keys (forces all services to fail loudly until re-provisioned)
gcloud iam service-accounts keys delete <KEY_ID> \
    --iam-account=rcg-prod-app@rcg-prod-12508.iam.gserviceaccount.com
```

Effect within seconds: every gcloud / SDK call from the NixOS box returns 401 Unauthenticated. Services that wrap errors (Sharadar script, signals-backup script) will log "GCS mirror: 0 ok, 13 failed" or equivalent — easy to detect on the operations dashboard.

After revocation, ping CCO. Then re-run `setup_sa_key.sh` from a clean box.

---

## Audit hooks

- Per `rcg_policy.md` §18, every key rotation + revocation logs to `decision_log/YYYY-MM-DD/sa-key-{rotation|revocation}/note.md`
- The key file is never checked into git (verified by `.gitignore` pattern `*.json` under `/etc/rcg/`)
- The key file's mtime is monitored by `rcg-ops-monitor` (alerts if file changes unexpectedly — TODO, not wired yet)

---

## If org-policy exemption is ever rolled back

If a future Workspace-admin sweep removes the exemption, key creation fails with:

```
PERMISSION_DENIED: Service account key creation is not allowed on this resource.
Constraint: iam.disableServiceAccountKeyCreation
```

In that case:
1. Get fresh CCO + MM approval
2. Re-apply the exemption from `docs/cco_sa_key_exemption_request.md`
3. Rotate

Until the exemption is restored, services continue using the existing key — no immediate operational impact.
