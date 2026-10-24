# OS update installer: evidence and pending cases

This covers plan tasks 5 and 6 (design §7–§8), and applies to protocol-2 cards only. On protocol-1 cards, Settings says a one-time reflash is needed and nothing is blocked.

## Implemented

- **hostd actions.** `os_status`, `os_check`, `os_install` (exact sequence and SHA-256, idempotent by request ID), `os_cancel` (until installing starts) and `os_dismiss`.
  - The app-update API is unchanged, and `update_apply(target='os')` still refuses.
  - No synchronous request waits for an install.
- **The worker.** `epilykos-os-update.service` does the work, not hostd, so a hostd restart neither stops nor duplicates a job.
  - hostd starts it for an admitted job, and starts it again when it notices the worker has gone: at its own start, or on a status poll.
  - Interrupted downloads resume; an interrupted install is reconciled.
- **Admission.** Before a job is admitted:
  - the running slot must have been confirmed healthy this boot (boot health records it);
  - no earlier job may still be pending in the environment;
  - the default boot order must be the running slot;
  - the running app must support `guard-v1` (label, environment variable, read-only mount) as well as `flock-v1`;
  - the package must be newer than both the floor and the running slot;
  - DATA must keep at least max(10 %, 1 GiB) free after the package plus a reserved full slot for the next app image. The image import has not been measured yet (design §8.3).
- **Journal.** It is fsynced and written by rename, with a directory fsync. It holds:
  - the request ID;
  - the exact signed entry and policy;
  - the source and target slots;
  - progress;
  - RAUC's identity: the job as RAUC transaction ID, the bus owner and the service invocation, all recorded before RAUC is called.
- **Download.** Data is streamed in 1 MiB writes into owned staging (`private/staging`, 0700, `O_NOFOLLOW`).
  - A download resumes only with a strong validator (`If-Range`) and the exact `206 Content-Range`. Otherwise it starts again.
  - It is never longer than the signed size.
  - Free space is checked while it runs.
  - It can be cancelled.
- **Verify, then install.** A newer root-signed policy is admitted first and kept even if the job fails, and RAUC is restarted to pick it up. Then:
  1. The exact bytes, the actual signer and the signed manifest are checked (`epilykos_os_trust`).
  2. `rauc install --transaction-id=<job>` runs.
  3. The installed slot is checked independently: its first image-size bytes must hash to the signed image SHA-256.
  4. It must still be non-bootable (`BOOT_<target>_LEFT=0`, with the boot order unchanged).
  5. Only then is the job `ready`. Its guard turns `pending` before the maintenance lease is released.
- **Failures.** The target is marked bad again and read back. If that cannot be shown, the job is `reconciling` (repair required), never a guessed success.
- **Lost worker during install.** Only the recorded RAUC writer (same bus owner and invocation) can complete the job, and then only with no `LastError` and a verified slot. A different, restarted or absent RAUC service means the install was interrupted, and the slot is marked bad.
- **Guard.** `public/guard.json` is mounted read-only into the app at `/run/epilykos-os-updater`. hostd rebuilds it from the journal at start, before the app starts. A missing or malformed guard reads as `reconciling`. hostd refuses:
  - while `active`: restart, shut down, factory reset, port and app restarts, app updates;
  - while `pending`: restart, shut down and app updates. A factory reset is still allowed, because DATA and the job go and the old slot boots;
  - while `reconciling`: the same as `active`.

## Tests

| Test | What it runs | Result |
|---|---|---|
| `tools/test_os_update.py` (29) | Signed packages (OpenSSL CMS in RAUC's layout); local HTTP server with Range/If-Range and faults; stand-ins for RAUC, systemctl, busctl, podman and fw tools. Covers admission, idempotency, resume, ignored or wrong range, missing validator, tampered or oversized downloads, DATA filling up, cancellation, RAUC failure, wrong slot contents, bootable target, unprovable state, maintenance busy, trust advancement kept after failure, recorded/foreign/absent RAUC after a lost worker, a killed worker resuming its download, guard rebuild, hostd refusals, permissions and image wiring | pass |
| `tools/test_boot_health.py` (20) | Only a confirmed ordinary boot is recorded for admission | pass |
| `tools/test_os_bundle.py`, `test_native_platform_env.py` | Real RAUC 1.15.2 and guarded fw-tools (from task 4) | pass |

## Pending

- The app side of `guard-v1`: the label, refusing conflicting app maintenance from the guard, and the Settings UI. Until the running app supports it, OS installation is not offered.
- Activation, Restart, Discard and confirmation (task 7).
- On the spare card:
  - a real install over Wi-Fi;
  - killing hostd, the worker and RAUC separately during a write;
  - pulling power while downloading and while installing;
  - measuring the bundle size, the app image import, the time and the RAM.
