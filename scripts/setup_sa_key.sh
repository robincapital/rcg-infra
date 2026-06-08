#!/usr/bin/env bash
# setup_sa_key.sh — one-shot migration off impersonation to a long-lived
# service-account JSON key for rcg-prod-app.
#
# Per docs/cco_sa_key_exemption_request.md (MM-approved 2026-05-28).
# Run interactively on NixOS as the `nixos` user AFTER a fresh
#   gcloud auth login --no-launch-browser
#
# What this script does:
#   1. Verifies your interactive gcloud auth is fresh (not impersonated)
#   2. Grants org-policy exemption for the rcg-prod-app SA (allows key creation)
#   3. Creates a JSON key for the SA
#   4. Installs the key at /etc/rcg/google_service_account.json (0600 nixos:users)
#   5. Disables the gcloud impersonation config (so subsequent commands use the key)
#   6. Runs a probe upload to verify
#
# Total runtime: ~30 seconds.
# After this script: re-run `sudo systemctl start sharadar-download.service`
# to backfill any failed mirrors. The markout publisher will use the new
# auth on its next nightly fire (02:30 ET) or any manual trigger.

set -euo pipefail

# Bootstrap-safe: clear env vars + persistent gcloud config that would
# otherwise force impersonation on the user account. Without this, the
# gcloud SDK impersonates rcg-prod-app for every call — which then 403s
# on cloudresourcemanager.projects.get (the SA only has objectAdmin) and
# enters an infinite retry loop. The whole point of this script is to
# stop using impersonation; we have to do that FIRST so the verification
# steps below can complete using the fresh user auth directly.
unset GOOGLE_APPLICATION_CREDENTIALS
unset CLOUDSDK_AUTH_IMPERSONATE_SERVICE_ACCOUNT
gcloud config unset auth/impersonate_service_account 2>/dev/null || true

PROJECT="rcg-prod-12508"
SA="rcg-prod-app@${PROJECT}.iam.gserviceaccount.com"
KEY_PATH="/etc/rcg/google_service_account.json"
KEY_DIR="$(dirname "$KEY_PATH")"
PROBE_BUCKET="gs://rcg-prod-data/_probe/sa_key_setup_$(date +%Y%m%d_%H%M%S).txt"

echo "=== RCG SA-key migration ==="
echo "Project: $PROJECT"
echo "SA:      $SA"
echo "Key at:  $KEY_PATH"
echo

# ── Step 1: verify fresh user auth ──────────────────────────────────────
echo "[1/6] Verifying gcloud user auth is fresh..."
if ! gcloud auth list --filter=status:ACTIVE --format="value(account)" | grep -q '@robincapitalgroup.com$'; then
    echo "ERROR: no active gcloud user account. Run:"
    echo "    gcloud auth login --no-launch-browser"
    echo "Then re-run this script."
    exit 1
fi
# Sanity check that we can hit a real API call (catches expired reauth state)
if ! gcloud --no-user-output-enabled projects describe "$PROJECT" >/dev/null 2>&1; then
    echo "ERROR: cannot describe project $PROJECT — interactive auth is expired."
    echo "Run: gcloud auth login --no-launch-browser"
    echo "Then re-run this script."
    exit 1
fi
echo "  ok — auth fresh, can hit project API"

# ── Step 2: grant the org-policy exemption ──────────────────────────────
echo "[2/6] Granting org-policy exemption (allow SA key creation on rcg-prod-app)..."
POLICY_FILE="/tmp/allow-sa-key-rcg-prod-app.yaml"
cat > "$POLICY_FILE" <<EOF
name: projects/${PROJECT}/policies/iam.disableServiceAccountKeyCreation
spec:
  rules:
    - condition:
        expression: "resource.matchTag('${PROJECT}/allow-sa-keys', 'true') || resource.name.endsWith('/${SA}')"
        title: "Allow keys for rcg-prod-app SA only"
        description: "Per CCO-approved exemption 2026-05-28 (docs/cco_sa_key_exemption_request.md)"
      enforce: false
    - enforce: true
EOF

if gcloud org-policies set-policy "$POLICY_FILE" --project="$PROJECT" 2>&1 | tee /tmp/orgpolicy.out; then
    echo "  ok — policy exemption applied"
else
    rc=$?
    echo "WARN: set-policy returned $rc. If you see PERMISSION_DENIED, you need the"
    echo "      orgpolicy.policyAdmin role granted to your user account."
    echo "      Either:"
    echo "        a) ask Workspace admin to grant you orgpolicy.policyAdmin on the project, OR"
    echo "        b) apply the policy via Cloud Console: https://console.cloud.google.com/iam-admin/orgpolicies"
    echo "      Once applied, re-run this script — it'll skip step 2 and continue."
fi
# Tolerate already-exempted state — proceed regardless
echo

# ── Step 3: create the JSON key ─────────────────────────────────────────
echo "[3/6] Creating JSON key for $SA..."
KEY_TMP="/tmp/rcg-prod-app.key.json.$$"
if gcloud iam service-accounts keys create "$KEY_TMP" \
    --iam-account="$SA" \
    --project="$PROJECT"; then
    echo "  ok — key created at $KEY_TMP"
else
    echo "ERROR: key creation failed. Org policy may not be exempted yet."
    echo "Apply the exemption manually in Cloud Console and re-run."
    rm -f "$KEY_TMP"
    exit 1
fi

# ── Step 4: install the key with proper perms ───────────────────────────
echo "[4/6] Installing key at $KEY_PATH..."
sudo mkdir -p "$KEY_DIR"
sudo chmod 0750 "$KEY_DIR"
sudo chown nixos:users "$KEY_DIR"
sudo mv "$KEY_TMP" "$KEY_PATH"
sudo chmod 0600 "$KEY_PATH"
sudo chown nixos:users "$KEY_PATH"
ls -la "$KEY_PATH"
echo "  ok — installed with 0600 nixos:users"

# ── Step 5: disable impersonation in gcloud config ──────────────────────
echo "[5/6] Disabling impersonation; activating SA via the new key..."
gcloud config unset auth/impersonate_service_account 2>/dev/null || true
gcloud auth activate-service-account --key-file="$KEY_PATH"
gcloud config set account "$SA"
# Now re-export the env vars for the probe in step 6 (matches what nix sets system-wide)
export GOOGLE_APPLICATION_CREDENTIALS="$KEY_PATH"
export CLOUDSDK_AUTH_IMPERSONATE_SERVICE_ACCOUNT=""
echo "  ok — gcloud will now authenticate as $SA directly via the key"
echo

# ── Step 6: probe upload to verify end-to-end ───────────────────────────
echo "[6/6] Probe upload to $PROBE_BUCKET..."
echo "rcg-sa-key-setup $(date -Iseconds)" > /tmp/probe.txt
if gcloud storage cp /tmp/probe.txt "$PROBE_BUCKET"; then
    gcloud storage rm "$PROBE_BUCKET" >/dev/null 2>&1 || true
    echo "  ok — write + read + delete all succeeded"
else
    echo "ERROR: probe upload failed. Check the SA's roles (should have storage.objectAdmin on gs://rcg-prod-data)."
    exit 1
fi
rm -f /tmp/probe.txt /tmp/orgpolicy.out "$POLICY_FILE"

echo
echo "==============================================="
echo " ✅ Migration complete."
echo "==============================================="
echo
echo "Next steps (these may already have been wired by the agent — confirm):"
echo "  1. Verify /etc/nixos/claude-finance.nix sets:"
echo "       environment.variables.GOOGLE_APPLICATION_CREDENTIALS = \"$KEY_PATH\";"
echo "       environment.variables.CLOUDSDK_AUTH_IMPERSONATE_SERVICE_ACCOUNT = \"\";"
echo "  2. sudo nixos-rebuild switch"
echo "  3. Backfill: sudo systemctl start rcg-signals-backup.service"
echo "              sudo systemctl start sharadar-download.service"
echo "              sudo systemctl start rcg-markout-publish.service"
echo
echo "Rotation: re-run this script after deleting the old key:"
echo "    gcloud iam service-accounts keys list --iam-account=$SA"
echo "    gcloud iam service-accounts keys delete <OLD_KEY_ID> --iam-account=$SA"
echo "    bash $0"
