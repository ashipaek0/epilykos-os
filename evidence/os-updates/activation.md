# Finishing OS updates: evidence and pending cases

This covers plan task 7 (design §7.2–§7.5, §9), and applies to protocol-2 cards only.

## Implemented (`epilykos_os_activate.py`)

- **Restart** (`os_restart`, for a ready job). hostd records the request and the worker does the work under the maintenance lease:
  1. It revalidates the current shared trust (a revoked key stops it), DATA space, the running app's `guard-v1` support, and the installed slot itself (its hash, and that it is still non-bootable).
  2. It writes activation intent to the redundant environment in one batch: `epk_os_pending`, target and source, signed sequence and image SHA-256, `epk_os_phase=activation_intent`.
  3. It runs `rauc status mark-active <target>`, then reads back `BOOT_ORDER` and `BOOT_<target>_LEFT=3`.
  4. It sets `activated` and reboots.

  Any failure before the reboot leaves the job `ready`: the target is non-bootable again and the pending state is cleared. When that cannot be shown, the job is `reconciling`.
- **Trial.** The loader spends an attempt and marks `epk_os_trial_started` (boot-slot.cmd). Boot health hands a healthy trial to `epilykos-os-confirm`.
- **Confirmation.** Each step is idempotent:
  1. `confirming`;
  2. `rauc mark-good`;
  3. the floor and `promoting`, written in one batch;
  4. app promotion on DATA;
  5. the journal set to `confirmed` and the guard to idle;
  6. the pending state cleared.

  A cut at any point is completed by the next confirmation or by reconciliation.
- **Every boot** (hostd, before the app):
  - Target booted: the trial is running, so the guard is `pending`.
  - Source booted after the trial started: rollback. The target is marked bad, the source made primary again, the floor is unchanged, and the job is `rolled_back`.
  - Source booted before any trial (a cut around Restart): the job is `ready` again, not a rollback.
  - DATA erased mid-trial (factory reset) or lost: the job is rebuilt from the environment.
- **Discard** (`os_discard`, ready jobs only, never once activation has started). The target stays bad and the staging is removed.
- **Generic restart and shutdown** are refused while a job is ready; it finishes only through its own Restart or Discard. They are allowed during the trial.
- **The app.** Nothing is promoted during a trial. After OS confirmation, a newer bundled app (same channel) becomes the promoted app, so a later fallback keeps it, while a newer app from Settings stays. On fresh DATA the signed baseline is used.

## Release rule (no destructive migration)

An OS release may bundle only an app whose database the actual confirmed fallback app can still read. There is no automatic database restore or rollback.

The release job (task 11) must prove this: migrate a representative database with the trial app, then start, read and write it with the real retained fallback app. Dev pairs are included. A `db-compat` label alone is not proof.

## Tests

| Test | What it runs | Result |
|---|---|---|
| `tools/test_os_reconcile.py` (17) | With a simulated slot loader: success with intent before mark-active; failed trial and rollback with the floor unchanged; cuts before and after the env write; revalidation failures (revoked trust, changed slot, refused mark-active, old app); unwritable state goes to `reconciling`; confirmation resumed from each phase; a cut after the journal before the env clear; confirmation only for the running trial; factory reset during the trial and after a rollback; discard; reboot rules; app promotion rules; hostd actions | pass |
| `tools/test_boot_health.py` (20) | A confirmed trial is recorded for the next admission | pass |

## Pending (spare card)

- The real Pi matrix: OS success and failure × app success and failure. Compare DATA markers, settings and the database, and check the actual selected image IDs.
- Power cuts at each activation and confirmation step on hardware.
- The migrated-database fallback test against a real retained fallback app.
