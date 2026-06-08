# CCO Request — Service Account Key Org-Policy Exemption

**Task ID:** infra-2026-05-28-001
**From:** RCG Quant Agent (acting under MM direction)
**To:** Ashley Schott (CCO)
**MM:** Nick Diaz (approval given 2026-05-28)
**Date:** 2026-05-28
**Routing:** Per `docs/rcg_policy.md` §24 (Vendor & service provider oversight) — IAM policy change on existing approved vendor

---

## Ask

Grant a one-account exemption to the GCP organization policy `iam.disableServiceAccountKeyCreation` for the production service account:

```
rcg-prod-app@rcg-prod-12508.iam.gserviceaccount.com
```

Once exempt, the agent will generate a single JSON key, mount it on the NixOS box via `GOOGLE_APPLICATION_CREDENTIALS`, and rotate the existing impersonation-based auth path off the affected services.

---

## Trigger

`docs/rcg_policy.md` §24 — IAM/auth model changes on existing approved vendors. GCP is on the approved-vendor list; this is a configuration change to how an existing GCP service account is authenticated, not a new vendor or new data flow.

## Background

The NixOS production box currently authenticates to GCS via **service-account impersonation** rather than a long-lived JSON key. This was the right initial choice (modern security posture, matches Google's recommendation when feasible). Per `CONTEXT_signal_capture.md` Session 1 log:

> "Pivot from JSON key to ADC — org policy `iam.disableServiceAccountKeyCreation` blocks long-lived keys (good thing — it's the modern security posture). Switched to gcloud ADC for both Windows and NixOS."

Six weeks of production operation has revealed an operational problem with this setup.

## The operational problem

Google Workspace enforces a **periodic re-authentication requirement** on the upstream user account whose token is used to impersonate the service account. Empirically the interval is ~4-6 hours. When that window elapses without an interactive `gcloud auth login` from the workstation:

- `gcloud storage cp` fails with `Reauthentication failed. cannot prompt during non-interactive execution`
- All systemd-driven GCS writers fail silently (they redirect stderr to /dev/null per the wrapper script pattern)
- Failures so far this week:
  - **Sharadar GCS mirror:** large parquets (DAILY 256MB, SEP 393MB, SF1 233MB, SF2 215MB, SF3 319MB, SFP 196MB) intermittently fail
  - **`rcg-signals-backup.service`** (nightly pg_dump → GCS, 30-day retention): partial failures observed when auth expires between 03:00 ET window and the next user login
  - **`rcg-markout-publish.service`** (new nightly): GCS archive step always lands the local JSON but the archive upload fails
- Operationally we end up needing to interactively re-auth roughly **every business day** to keep the night runs healthy

## Why JSON key is the right move now

This is not a security regression — it's the canonical pattern for non-interactive server workloads:

1. **No additional access** — the SA still has only `roles/storage.objectAdmin` on `gs://rcg-prod-data`. The key just changes how *we* prove we are the SA, not what the SA can do.
2. **Key lives on a single box.** The NixOS production box at home. No second copy. Not synced to Dropbox, GitHub, or any other location.
3. **Permissions on the key file:** `chmod 600`, owner `nixos:users`. Only the `nixos` user can read it.
4. **Logged for audit.** A `decision_log/` entry will record the exemption + key generation + this CCO request.
5. **Rotation plan.** Quarterly rotation (manual on the calendar) — generate new key, redeploy, delete old. CCO gets notified each rotation.
6. **Revocation path.** If we ever want to roll back, delete the key from the SA — that immediately invalidates it; no cleanup needed beyond removing the file.

## What does NOT change

- Approved vendor list unchanged (GCP already on it per `docs/rcg_policy.md` standing reference data)
- Data flowing through GCS unchanged (Sharadar mirror, db_backups, bloomberg archive, markouts archive — all internal-use signals)
- No client data touches GCS (per CCO + MM standing rules)
- IB / brokerage integration unchanged
- DAPI / Bloomberg posture unchanged
- §17 (advertising) unchanged
- §13 (MNPI) unchanged

## Specific permissions ask

The org policy exemption is **scoped to the single SA** `rcg-prod-app@rcg-prod-12508.iam.gserviceaccount.com`, not org-wide. Implementation:

```
gcloud resource-manager org-policies set-policy \
    --project=rcg-prod-12508 \
    --policy-from-file=allow-sa-key-rcg-prod-app.yaml
```

Where the policy file scopes the allow to that one SA via condition. Other SAs in the project (if added later) remain blocked by the default deny.

## Per `rcg_policy.md` decision matrix

| Change | Routing |
|---|---|
| Approved vendor IAM/auth change (GCP, existing SA, no new permissions, no new data) | MM proposes → CCO informed → CCO can object, otherwise MM approves |

This sits in the MM-decides bucket. MM approval given 2026-05-28 by Nick Diaz in conversation with the agent. This document surfaces the change to CCO as the policy mandates.

---

## CCO action requested

Either:
1. **Acknowledge** — the agent proceeds with key generation + rotation off impersonation. (If no objection within 5 business days, MM treats as acknowledged per standing practice.)
2. **Object** — propose an alternative (e.g., Workload Identity Federation if you prefer to avoid keys entirely). The WIF path is heavier-lift but does eliminate key handling.

Either response goes in `decision_log/2026-05-28/infra-2026-05-28-001/`.

---

## Appendix — observed failure log (truncated)

```
2026-05-28 17:31  systemd rcg-filtered-revalidate    ok
2026-05-28 17:43  systemd rcg-screener-long          ok (no GCS calls)
2026-05-28 18:00  systemd rcg-bloomberg-pull         ok (no GCS calls)
2026-05-28 18:18  systemd rcg-markout-publish        local ok; GCS upload exception
                  → FileNotFoundError: 'gcloud' (also PATH fix issue, separate)
2026-05-28 18:23  systemd sharadar-download          local ok; GCS mirror: 0 ok, 13 failed
                  → all 13 partitions failed today (was 7 ok yesterday before token expiry)
2026-05-28 18:25  manual  gcloud storage ls          ERROR: Reauthentication failed
```

Every failure traces to the same root: workspace-level interactive reauth requirement on the impersonation token chain.
