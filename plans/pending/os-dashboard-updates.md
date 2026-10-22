# OS Dashboard Updates Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. The maintainer selects the execution method; this planning request does not authorize implementation.

**Goal:** Install authenticated OS releases from Settings into an inactive, self-contained Linux slot, with truthful progress, explicit restart, automatic failed-candidate rollback and retained DATA/application state.

**Architecture:** A systemd-owned OS job coordinates signed release metadata, private DATA staging, the existing shared maintenance lock, durable guards and RAUC. U-Boot loads Linux from the chosen ROOT; an early userspace health/watchdog supervisor confirms it. A separate boot-chain/recovery workstream satisfies the already locked v1 recovery scope.

**Tech Stack:** Existing Buildroot 2026.05.3, Linux 6.6.31, U-Boot 2025.10, RAUC 1.15.2, systemd, Python/dbus-fast, Node/Express and current plain JavaScript Settings UI. No change of application framework.

**Spec:** [Proposed OS dashboard update design](../decisions/os-dashboard-updates-design.md). Read it and [locked v1 policies](../decisions/epilykos-os-v1-scope.md) before execution. Exact choices labeled proposed need design approval, not silent interpretation as prior user decisions.

## Decisions of 2026-10-22

- D1: slot updates (tasks 2–8, then 9 and 11 for them) ship once they pass on hardware; the boot chain is fixed at flashing; task 10 is Track B and does not gate them. Where the constraints below require fully automatic early recovery or both-slot recovery, read them as Track B requirements.
- D2: the maintainer keeps the CA and index keys offline (custody prerequisite met; key generation is a maintainer step in task 4).
- D3: a spare SD card is available; no power-control rig or serial cable.
- Task 1's first step is done: `dev` contains `main` (OS PRs #82, #83).
- The factory reset (OS #81) is merged; the design (§7.5) covers its interaction with updates.

## Global Constraints

- Pi 3B first; MBR, fixed existing PARTUUIDs, four existing partitions. No silent card migration, fifth partition or DATA erase.
- Kernel, Linux DTB, modules and rootfs are one update/rollback unit per slot.
- One 1200-second deadline from Linux userspace startup, including application import/checks/migrations; three candidate attempts, then known-good fallback. Network and inverter availability do not gate confirmation.
- Fully automatic early bad-loader recovery and independent both-slot/DATA-failure recovery remain v1 requirements; their feasibility is not assumed.
- Native RAUC signatures, separate development/release CAs, offline CA custody, protected CI signer; app and OS-index keys separate. Never a verification bypass.
- OS installation and activation are authenticated/manual; auto-check or app auto-install does not authorize either. USB insertion and reconnect do not authorize installation.
- Warn below max(20% DATA capacity, 2 GiB); projected peak maintenance headroom max(10%, 1 GiB); bounded RAM and DATA staging.
- Preserve external replay floors, internal known-good rollback and newer compatible same-channel apps; no destructive implicit database rollback.
- Existing maintainer merge authorizes publication, followed by meaningful automated gates. Current QEMU remains diagnostic; no new per-image manual approval.
- Deliver OS changes through restored `dev`, app changes through app `dev`. Reconcile OS main-only stable pin and networking changes first. No merge/publish/hardware mutation solely under this plan request.

## Review Focus

1. RAUC still writing after hostd/worker death: a new restore/app/reset job must remain refused until its transaction is proven terminal (Task 6).
2. Power loss between activation, mark-good and floor/result persistence: booted identity and permitted rollback must reconcile without accepting replay or losing DATA (Tasks 2, 3, 7).
3. Newer app or partially migrated DB on the rollback OS: no silent app downgrade, data snapshot restoration or false successful fallback (Task 7).
4. Unknown clock, lost control records or old recovery trust: fail closed under the approved offline policy without an unauthenticated repair escape (Tasks 4, 9, 10).
5. Disk fills from monitoring while staging/installing, or browser/USB disappears: preserve active slot/data, own incomplete files and report an actionable resumable job result (Tasks 5, 6, 8).

## Tracking and verification conventions

All tasks below are **pending**. Proposed test modules/commands do not exist yet and are not evidence of passing behavior. Existing checks: OS `make test contracts`, `git diff --check`; app `npm test` with its CI-supported Node version (inspect current package/workflow before installing). Use the existing OS virtual environment or CI dependencies when running Python tests; system Python previously lacked dbus-fast.

For every implementation task: create its focused regression first, run it and observe the intended failure, implement the smallest slice, run the focused test and required affected-suite checks, then commit explicit paths as ashipaek0 with no AI attribution. Do not write tests that only duplicate a configuration string; include actual generated-artifact or process behavior where that is the failure mode.

| Task | Size | Dependencies | Status / exit evidence |
|---|---|---|---|
| 1. Freeze baseline and platform feasibility | M | Design approval | Pending; physical boot/recovery ADR and measured budgets |
| 2. Slot-contained Linux | M | 1 for final boot ABI | Pending; two actual kernel builds boot from distinct ROOTs |
| 3. Health deadline and recovery routing | M | 2, recovery selector from 1 | Pending; failed app/kernel/hang resets, bounded fallback |
| 4. Trust and package format | M | 1's trust/control layout | Pending; real signed RAUC package positive/negative tests |
| 5. Host staging and admission | M | 4 | Pending; byte/hash/space/ownership proofs |
| 6. RAUC transaction and durable guard | M | 2–5 | Pending; worker-kill/RAUC-live concurrency tests |
| 7. Activation, reconciliation and app safety | M | 3, 6 | Pending; success/failure/OS-app matrix and crash cuts |
| 8. Settings end-to-end online update | M | 5–7, paired app capability | Pending; authenticated UI → real host job → reboot result |
| 9. Authenticated offline imports | M | 4–8 | Pending; network-blocked upload/USB installation |
| 10. Independent recovery and boot-chain updater | M per sub-PR | Proven 1, 3, 4, 9 | Pending; all locked bad-loader/both-slot tests |
| 11. Publication and acceptance gates | M per sub-PR | 2–10 | Pending; complete signed release, Pi failure matrix and soak |

These are review boundaries, not permission to put all files of a subsystem in one oversized PR. Split a task if its diff exceeds a focused review; preserve the interfaces below. Checkpoint after Tasks 1–3, 4–7 and 8–11.

### Task 1: Baseline, bootstrap and physical feasibility

**Files:** modify this plan as evidence arrives; create `plans/decisions/pi3-update-bootstrap.md`, `tools/bootstrap_export.py`, `tools/test_bootstrap_adoption.py`, `tools/hardware/boot_chain_acceptance.py`, `tools/hardware/device-config.example.json`, `evidence/os-updates/pi3-feasibility.md`; inspect current board files. Experimental loader code belongs in a separate spike branch until the boundary is approved.

**Interfaces:** produces an approved `boot_protocol=2` ABI, non-overlapping control-record/recovery layout, earliest protected component list, recovery credential lifecycle and bootstrap adoption procedure. Consumer: Tasks 2, 4, 10. No fabricated offsets or passing evidence.

- [ ] Fetch actual OS/app remote refs; verify restored OS `dev` and reconcile main-only stable sequence 7/provenance and network/Improv fixes in a reviewed sync. Run existing affected tests; preserve current signed app pins.
- [ ] Implement a hardware runner that records board/media/build/bootloader identities and explicit pending results when a test cannot run. Example interface: `python3 tools/hardware/boot_chain_acceptance.py --device-config DEVICE.json --case selector-fallback`.
- [ ] On a spare test card with authorized hardware access, prove corrupt/returning/hanging candidate loader fallback without manual cycling, plus watchdog handoff. Record which failures cannot be recovered before the selector starts. No serial cable or switched-power rig is currently assumed available.
- [ ] Build/measure independent recovery payload against 128 MiB BOOT and control state against the existing pre-BOOT gap; demonstrate no MBR/environment overlap. Validate both Linux slots and factory-reset preservation of trust/floors.
- [ ] Specify source export and target adoption with tests: authenticated receipt versus database-only restore, same-channel app baseline below/equal/above source floor, new recovery enrollment, prior OS/trust floor, missing source state and signed per-device adoption ticket. Do not invent a legacy dashboard export endpoint or silently reset ordering. Run `python3 tools/test_bootstrap_adoption.py` when that slice is implemented.
- [ ] Commit ADR/evidence with a go/no-go conclusion. If the protected boundary cannot meet the locked requirement, stop that workstream for a measured maintainer decision; continue independent package/UI tests with that limitation explicit.

### Task 2: Build and load complete Linux slots

**Files:** modify `os/board/epilykos/raspberrypi3/{post-build.sh,post-image.sh,boot.cmd,uboot.fragment,genimage.cfg}`, `os/configs/epilykos_raspberrypi3_defconfig`, `.github/workflows/os-build.yml`; create `tools/test_slot_linux.py` and `tools/check_slot_linux.py`.

**Interfaces:** immutable `/usr/lib/epilykos/platform.json` includes `schema=1`, `board=raspberrypi3`, `arch=arm64`, `slot_layout=pi3-mbr-v1`, `boot_protocol=2`, channel, OS release/sequence/source hash, kernel release and host API version. Paths for slot Image/DTB are fixed in the design. Boot slot names remain A/B and existing PARTUUIDs remain unchanged.

- [ ] Add failing tests `test_slot_artifacts_match_modules`, `test_selected_slot_supplies_both_linux_files`, `test_missing_dtb_never_reuses_prior_ram`, `test_counter_save_failure_enters_recovery`; fixture both distinct release kernels and invalid loads.
- [ ] Stage kernel/DTB before rootfs packing; enable and exercise U-Boot ext4 loading; save counter before launch, check each result and route failed loads deterministically. Keep required pre-U-Boot firmware artifacts separate.
- [ ] Update image structural checks to inspect both ROOT filesystems, matching module ABI/artifact hashes and capacity, rather than requiring shared Linux Image/DTB on FAT as the normal boot source.
- [ ] Run `python3 tools/test_slot_linux.py`, `make test contracts`, full image build and the actual generated-slot checker. Then boot A with one kernel and B with another on the Pi, recording `/proc/cmdline`, `uname -r`, module loading and DATA/settings identity.
- [ ] Commit the slice; retain old card and publish experimental artifacts only under accurate support labels until acceptance.

### Task 3: Bounded boot health and watchdog ownership

**Files:** replace internals of `rootfs-overlay/usr/bin/epilykos-mark-good`; modify its service/preset, `boot.cmd`, `linux.fragment`, `uboot.fragment`; create `rootfs-overlay/usr/lib/epilykos/python/epilykos_boot_health.py`, a systemd watchdog drop-in, `tools/test_boot_health.py` and hardware cases. All overlay paths are below `os/board/epilykos/raspberrypi3/`.

**Interfaces:** health supervisor consumes boot/platform identity and monotonic userspace start/deadline, checks DATA/Podman/app health, emits exactly one confirmation/failure to Task 7's reconciliation API. It owns a single 1200-second deadline; it must not wait until `epilykos.service` finishes before starting that clock. Runtime hardware watchdog target 10 seconds, not 1200 seconds; actual supported timeout must be measured.

- [ ] Add failing cases for slow OCI import within budget, stalled migration at 1200 seconds, PID1 alive but unhealthy app, kernel hang, unavailable DATA, both slots exhausted, lost environment and no Internet/inverter. Assert exactly three candidate attempts and no counter-refill loop.
- [ ] Implement early supervision, bounded reboot plus hardware fallback, and recovery classification. Preserve RAUC as the normal runtime slot-state writer; fail safely on state-write errors.
- [ ] Run `python3 tools/test_boot_health.py`; verify systemd dependency graph has no after-app deadline or recovery/mark-good cycle. Run real Pi hang/deadline/A-B fallback tests; process-kill-only tests do not prove kernel/early reset coverage.
- [ ] Commit with measured handoff coverage and remaining physical limitations recorded.

### Task 4: Native signed bundles and release authorization

**Files:** modify RAUC `system.conf`, defconfig/kernel requirements and board build hooks; create `tools/{build_os_bundle.py,test_os_bundle.py,verify_os_release.py,test_verify_os_release.py}`, `schemas/os-release.schema.json`, `keys/os-release/README.md`, public test-only CA fixtures and OS-index public key provisioning. Production secrets stay outside the repo.

**Interfaces:** `verify_os_release(entry_bytes: bytes, signature: bytes, platform: dict, trust_state: dict) -> dict` validates the signed entry described by the spec. RAUC InspectBundle supplies native manifest hash/metadata; returned values are compared, not accepted from the browser. `build_os_bundle` derives bundle and flash rootfs from the same bytes.

- [ ] Add real RAUC fixtures for valid release, unsigned/tampered/wrong CA/dev signer, wrong board/channel/protocol, unexpected image/handler, wrong signed pair, oversized rootfs, replay, unknown schema and certificate-time/rotation policy.
- [ ] Implement explicit verity bundles, pinned public keyrings, signed `[meta.epilykos]`, `activate-installed=false`, required host/target features and protected signing procedure. Verify actual RAUC 1.15.2 behavior; no guessed options or CLI text parsing.
- [ ] Run `python3 tools/test_os_bundle.py` and `python3 tools/test_verify_os_release.py` against produced packages; prove invalid packages cause no slot write. Verify native bundle hash/signer and target rootfs platform identity independently.
- [ ] Document the approved offline-time/revocation policy and key-operator handoff before committing. Real release key generation and GitHub secret provisioning require the agreed custody procedure; fixture keys are clearly non-production.

### Task 5: Owned staging, storage accounting and transfer protocol

**Files:** create overlay Python `epilykos_os_release.py`, `epilykos_storage.py`, `epilykos_os_upload.py`; new owned upload socket/service or hostd integration; create `tools/{test_os_staging.py,test_storage_admission.py}`. Reuse app maintenance storage policy instead of inventing different thresholds.

**Interfaces:** `admit_os(entry: dict, platform: dict) -> dict` returns required/available bytes and blockers. `stage_online(job_id: str, entry: dict) -> dict` yields only an owned verified package identity. `begin_upload(entry: dict, request_id: str) -> dict` yields opaque transfer ID/token and exact raw-stream length budget. Public errors never expose private paths/tokens.

- [ ] Add tests for quota boundary/headroom, unknown length, wrong hash/size, monitoring growth/ENOSPC, disconnected transfer, symlink/hardlink/path substitution, token replay, replaced USB file and concurrent admission. Assert no bundle appears in `/tmp` and memory does not scale with file size.
- [ ] Implement 1 MiB buffered DATA streaming, root-owned staging and transfer token protocol; enforce 64 KiB discovery-index, 8 KiB signed-entry and 16 KiB serialized control-header limits. Filesystem ownership is host-controlled; pin the file identity through verification/RAUC handoff.
- [ ] Run `python3 tools/test_os_staging.py` and `python3 tools/test_storage_admission.py`; measure disk allocations and peak RSS on bounded large fixtures. Cleanup removes only owned terminal orphan files, not journal-referenced candidates or protected assets.
- [ ] Commit with explicit disk-budget examples for actual bundle/rootfs/OCI sizes; do not use guessed compression ratios.

### Task 6: Durable RAUC installation and maintenance guards

**Files:** create overlay Python `epilykos_os_update.py`, `epilykos_rauc.py`, worker executable/service; modify `epilykos_update.py`, `epilykos-hostd`, `epilykos_maintenance.py`, reset admission and service presets; app companion changes in `modules/maintenanceLock.js` and existing maintenance entry points; tests `tools/test_os_update.py`, `tools/test_os_guard.py`, app `test/os-maintenance-guard.test.js`.

**Interfaces:** OS backend `status() -> dict`, `check() -> dict`, `apply(sequence: int, digest: str, request_id: str) -> dict`, `recover() -> None`; facade preserves app API. Guard schema 1 uses `idle/active/pending/reconciling`; `/data/epilykos/os-updater/public` is directory-bound read-only to `/run/epilykos-os-updater`, keeping private state unmounted. Required app label `io.epilykos.os-update-guard=guard-v1` and env `EPILYKOS_OS_UPDATE_GUARD=/run/epilykos-os-updater/guard.json` are verified along with existing flock-v1. RAUC transaction UUID is stored before invocation and reconciled after worker death.

- [ ] Write failing integration cases killing hostd, worker and RAUC separately during a write. Start a competing restore/app update/factory reset and prove it cannot proceed while the RAUC operation remains live, including after flock owner death.
- [ ] Implement systemd-owned jobs and D-Bus progress with durable journals/guards, matching RAUC service invocation/unique owner/transaction completion. A replacement idle service makes the old job interrupted, not successful; prove previous writers gone and target invalidated before clearing its guard. Refuse arbitrary paths, `automatic:true`, duplicate mismatched identities, other active maintenance and pending factory-reset requests. Preserve monitoring service during installation.
- [ ] Gate all cooperating admissions under flock plus guard; unknown/missing guard fails closed on a protocol-enabled host. Test that atomic guard replacement is visible through the directory mount and backups/snapshots allowed while pending exclude reboot until their leases close. Do not clear a guard from PID absence; prove RAUC terminal state and staged slot safety. Old guard-unaware app releases cannot enable this updater.
- [ ] Run `python3 tools/test_os_update.py`, `python3 tools/test_os_guard.py`, companion app fixtures and affected suites. Confirm no synchronous request waits for installation and browser close does not abort/duplicate it.
- [ ] Commit paired OS/app PRs with a backward-compatible capability rollout; expose install only after both halves are present.

### Task 7: Explicit activation, promotion and application retention

**Files:** create overlay Python `epilykos_platform_state.py`, `epilykos_app_selection.py`; modify OS updater/health gate, `epilykos-run`, `epilykos-image-load`, app updater bookkeeping, factory-reset handling; tests `tools/{test_os_reconcile.py,test_app_selection.py,test_platform_state.py}` and hardware reboot cases.

**Interfaces:** `select_app(platform: dict, promoted: dict|None, rollback_platform: dict) -> dict`; `request_reboot(job_id: str) -> dict`; `reconcile_boot() -> dict`; `confirm_candidate(job_id: str) -> dict`. Control-record serialization/offsets come from Task 1. Promotion ordering uses an idempotent intent/commit record, not independent unjournaled floor writes.

- [ ] Add a state-transition crash matrix: before/after mark-active, reboot, mark-good, floor write and result write; malformed/missing control state, factory reset, spontaneous reboot and manual discard. Assert floor never decreases, failed candidates do not raise it and known-good internal rollback still boots.
- [ ] Add app matrix tests: promoted app newer/older/equal to bundled, mixed channel, missing image, new host API requirement, new app against old OS, database migrated then OS rollback. Prove a newer failing bundled app remains tentative and old-slot fallback runs the previously confirmed app; whole-OS confirmation promotes the trial app/floor atomically. Reject combinations whose data cannot be read by that confirmed fallback app/rollback OS.
- [ ] Implement activation-intent persistence, exact-target readback, manual reboot, early boot reconciliation, safe app selection/bookkeeping and protected image retention. Guard is pending until confirmation/rollback; normal reboot without job activation stays on the old slot.
- [ ] Run focused tests and real Pi OS-success/failure × app-success/failure cases. Compare DATA markers/settings/DB and inspect actual selected image IDs, not UI text alone.
- [ ] Commit with compatibility metadata and the explicit no-destructive-migration release rule.

### Task 8: Settings online update journey

**Files (app):** `routes/updates.js`, `modules/updates.js`, `modules/hostActions.js`, `public/js/updates-settings.js`, relevant notification/result rendering; add `test/os-updates-http.test.js`, `test/os-updates-ui.test.js`. OS adds job-bound reboot/cancel/discard dispatch to `epilykos-hostd` and the facade; document actions in `docs/maintenance/os-update-actions.md`.

**Interfaces:** API/fields exactly as design §10. Existing `applying` still describes app jobs; new `osProgress` describes OS jobs. `/apply` must pass exact checked OS sequence/hash/request ID. New `/os/reboot`, `/os/cancel`, `/os/discard` are session/CSRF/password protected and job-bound. Check/status never authorize mutation.

- [ ] Add request and DOM tests: wrong password/session/origin rejected; app update behavior unchanged; OS installation longer than 15 minutes; browser refresh/disconnect; stale release selection; reboot-required state; resumed confirmed/rolled-back result; dismissal bound to job; unsupported old host; app auto-install cannot select OS.
- [ ] Implement capability-driven review/install/progress/restart/result states. Visible active polling is 2 seconds with connection backoff; no invented ETA, automatic activation on reconnect, or app-style restart assumption during staging.
- [ ] Run app tests and end-to-end app/host disposable integration. Rehearse the complete online flow on Pi, including authenticated restart and deliberately unhealthy candidate; monitoring remains active until restart.
- [ ] Commit the paired UI/API slice, retaining old-host and Docker/Compose fallbacks.

### Task 9: Online-equivalent upload and USB import

**Files:** app upload/import routes and UI in Task 8 files; overlay upload adapter and USB enumeration/import module; `tools/test_os_offline.py`, app `test/os-update-upload.test.js`.

**Interfaces:** same signed entry, bundle verifier, admission and job lifecycle as online. No archive extraction or browser-selected host paths. USB file selection is an enumerated opaque ID; media mounts are read-only `nodev,nosuid,noexec`. Insertion only discovers packages.

- [ ] Add network-blocked valid install plus wrong CA/channel/sequence, truncated/removable file, replaced pathname, disconnected browser, oversized input and invalid-clock policy tests. Assert no activation from mount/insertion or failed import.
- [ ] Implement authenticated bounded upload through the separate Unix stream, and owned USB copy; one verifier and one authorization model for all three delivery paths.
- [ ] Run `python3 tools/test_os_offline.py`, app upload fixtures, then real Pi offline install/reboot/rollback. Test certificate validity/rotation offline under the approved policy.
- [ ] Commit with documented package naming and a usable import failure/retry flow.

### Task 10: Independent recovery and dedicated boot-chain updates

**Files:** new `os/recovery/` minimal build/config, proven Task 1 selector/control records, separately signed boot-chain package tools and authenticated worker, recovery authorization/repair UI, `tools/test_recovery.py`, `tools/test_boot_chain_update.py`, `tools/hardware/` cases. Split recovery, authorization and boot-chain implementation into focused PRs.

**Interfaces:** consumes Task 1's approved immutable boundary/layout and Task 4's native package trust. Recovery boots from its measured BOOT allocation and reads credentials/control state without DATA. Repair has no ordinary rootfs or Podman dependency; both rejected/unfinished repair and lost credentials have documented safe outcomes.

- [ ] Test automatic entry with both ROOTs unreadable and DATA unmounted, recovery authorization failures, unsigned/wrong-board repair, offline signed reinstall and retained DATA. At the floor, allow only repair of the exact recorded highest-confirmed package; reject older/reused-sequence different-digest packages. Lost control records must not reset ordering to zero.
- [ ] Implement authenticated repair and trust rotation with rollback-safe recovery payload handling. Distinguish shared-DATA failure from two bad normal slots; never automatically format or start DB writes on unusable DATA.
- [ ] Implement the separate early boot-chain updater only inside the proven recovery boundary. Test truncated/corrupt/valid-but-hanging loader and power loss at write/select/confirm transitions. No ordinary RAUC rootfs bundle may mutate shared boot artifacts.
- [ ] Run focused host tests and the physical Pi suite. A manually cycled recovery, missing rig or emulator-only result remains pending; it does not satisfy fully automatic recovery.
- [ ] Commit each focused PR with evidence and exact protected components. Do not declare full v1 ready if this required workstream is still unproven.

### Task 11: Release gates and measured platform acceptance

**Files:** `.github/workflows/os-build.yml`, new protected OS signing/publishing workflow if separation is needed, `tools/image_release_policy.py`, `tools/test_release_gates.py`, package verification tools, `evidence/os-updates/`, hardware runner; update contract implementation/evidence statuses accurately and run rendering/checks.

**Interfaces:** release artifacts from the exact same rootfs include `.img.zst`, `.raucb`, immutable signed OS authorization and signature/checksums, Imager list and provenance/evidence references. Discovery pointer advances only after complete verified assets exist. Host preflight does not trust an unsigned checksum or a display version as ordering.

- [ ] Add failing publication cases: missing/tampered bundle, signature/index mismatch, source not authorized, reused version/sequence, wrong architecture, over-capacity rootfs/recovery, missing compatible app, secret access from PR context, incomplete upload and regressions in the produced slot loader.
- [ ] Implement protected signer/source gating, serialized sequence allocation, immutable-asset collision refusal and last-step signed-pointer publication. Re-run native bundle/slot checks on downloaded output artifacts. Keep current QEMU diagnostic.
- [ ] On real Pi record good update, broken kernel/DTB/app, all candidate attempts, both-slot recovery, shared DATA failure, interrupted download/write/activation/confirmation, key rejection/rotation, offline repair, preserved newer app and factory-reset interactions. Physical power-cut cases require separately authorized repeatable power-control hardware; do not improvise destructive tests on the user's only working card.
- [ ] Measure actual download/install duration, worker and RAUC peak RSS, headroom, monitoring latency and post-reboot outage with representative approximately 5.9 GB DATA and a 72-hour soak. Establish limits from that evidence before enabling production publication.
- [ ] Run `make test contracts`, app required tests, full image/package workflow and the configured hardware acceptance runner. Preserve machine-readable identities/results, logs and digests. Publish automatically after existing maintainer authorization only when required deterministic gates pass.
- [ ] Update progress/contract evidence and archive this plan only after the stated outcome and locked recovery gates actually pass.

## Delivery checkpoints and maintainer actions

- **Checkpoint A (1–3):** spare-card bootstrap and slot recovery proof; no normal-device rollout.
- **Checkpoint B (4–7):** real signed local bundle installation/reconciliation with retained app; still no claim of final production readiness.
- **Checkpoint C (8–11):** dashboard/online/offline/recovery and release automation accepted with real evidence.

Maintainer decisions/actions before execution: approve this design including the offline certificate-time policy; accept the recovery boundary/credential/storage ADR after the feasibility result; establish the release-key custodian/protected CI signing procedure; authorize and provide the spare-card Pi/power-control acceptance setup. Software planning and automated tests can proceed independently of unavailable physical equipment, but missing evidence remains a release gate.
