# OS Dashboard Updates Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. The maintainer selects the execution method; this planning request does not authorize implementation.

**Goal:** Install authenticated OS releases from Settings into an inactive, self-contained Linux slot, with truthful progress, explicit restart, automatic failed-candidate rollback and retained DATA/application state.

**Architecture:** A systemd-owned OS job coordinates native RAUC signatures plus delegated package authorization, private DATA staging, the shared maintenance lock and durable guards. U-Boot loads Linux from the selected ROOT; early userspace refreshes health on every boot. Shared trust policy, OS floor and bounded activation/promotion intent live in the existing redundant U-Boot environment. Track B separately owns early boot-chain/independent recovery.

**Tech Stack:** Existing Buildroot 2026.05.3, Linux 6.6.31, U-Boot 2025.10, RAUC 1.15.2, systemd, Python/dbus-fast, Node/Express and current plain JavaScript Settings UI. No change of application framework.

**Spec:** [Proposed OS dashboard update design](../decisions/os-dashboard-updates-design.md). Read it and [locked v1 policies](../decisions/epilykos-os-v1-scope.md) before execution. Exact choices labeled proposed need design approval, not silent interpretation as prior user decisions.

## Decisions of 2026-10-22

- D1: slot updates (tasks 2–8, then 9 and 11 for them) ship once they pass on hardware; the boot chain is fixed at flashing; task 10 is Track B and does not gate them. Where the constraints below require fully automatic early recovery or both-slot recovery, read them as Track B requirements.
- D2: the maintainer keeps the CA and index keys offline (custody prerequisite met; key generation is a maintainer step in task 4).
- D3: a spare SD card is available; no power-control rig or serial cable.
- Task 1's first step is done: `dev` contains `main` (OS PRs #82, #83).
- The factory reset (OS #81) is merged; the design (§7.5) covers its interaction with updates.

## Consistency revision of 2026-10-23

This plan is reconciled to the revised design. Its detailed tasks below, not
an overriding note alone, reflect D1–D3. Track A1 online release requires
tasks 1–8 and task 11's online checks/physical evidence. Track A2 adds task 9
and task 11's offline evidence. Task 10 is Track B and gates neither A1 nor A2.
It still gates any claim of full independent/early boot recovery.

## Global Constraints

- Pi 3B first; MBR, fixed existing PARTUUIDs, four existing partitions. No silent card migration, fifth partition or DATA erase.
- Kernel, Linux DTB, modules and rootfs are one update/rollback unit per slot.
- One 1200-second deadline from Linux userspace startup, including application import/checks/migrations; three candidate attempts, then known-good fallback. Network and inverter availability do not gate confirmation.
- Fully automatic early bad-loader and independent both-slot/DATA-failure recovery remain Track B requirements. Track A ends exhausted-slot boot safely without counter-refill loops; it does not claim that recovery is available.
- Native RAUC signatures plus signed package authorization on every delivery path. Offline CAs and index authorization root delegate scoped CI signing keys; shared policy epochs prevent local revocation rollback. Never a verification bypass.
- OS installation and activation are authenticated/manual; auto-check or app auto-install does not authorize either. USB insertion and reconnect do not authorize installation.
- Warn below max(20% DATA capacity, 2 GiB); projected peak maintenance headroom max(10%, 1 GiB); bounded RAM and DATA staging.
- Preserve external replay floors, internal known-good rollback and newer compatible same-channel apps; no destructive implicit database rollback.
- Existing maintainer merge authorizes publication, followed by meaningful automated gates. Current QEMU remains diagnostic; no new per-image manual approval.
- Deliver OS changes through `dev`, app changes through app `dev`. Main-to-dev sync is complete (#83); preserve it. No merge/publish/hardware mutation solely under this plan request.

## Review Focus

1. RAUC still writing after hostd/worker death: a new restore/app/reset job must remain refused until its transaction is proven terminal (Task 6).
2. Power loss between activation, mark-good and floor/result persistence: booted identity and permitted rollback must reconcile without accepting replay or losing DATA (Tasks 2, 3, 7).
3. Newer app or partially migrated DB on the rollback OS: no silent app downgrade, data snapshot restoration or false successful fallback (Task 7).
4. Unknown clock, lost env or revoked trust in a retained older slot: fail closed for installation under the shared trust policy, including offline (Tasks 1, 4, 7, 9). Track B later adds independent repair.
5. Disk fills from monitoring while staging/installing, or browser/USB disappears: preserve active slot/data, own incomplete files and report an actionable resumable job result (Tasks 5, 6, 8).

## Tracking and verification conventions

All tasks below remain **incomplete**. Task 1's first implementation slice
adds the portable native-env core and `tools/test_platform_env.py`, followed
by opt-in native U-Boot/fw-tools integration and `make test-native-env`; its
[delivery status](../decisions/pi3-update-bootstrap.md) distinguishes tested
primitives from pending target integration. Other proposed test modules/commands
are not evidence of passing behavior. Existing checks: OS `make test contracts`,
`git diff --check`; app `npm test` with its CI-supported Node version (inspect
current package/workflow before installing). Use the existing OS virtual
environment or CI dependencies when running Python tests; system Python
previously lacked dbus-fast.

For every implementation task: create its focused regression first, run it and observe the intended failure, implement the smallest slice, run the focused test and required affected-suite checks, then commit explicit paths as ashipaek0 with no AI attribution. Do not write tests that only duplicate a configuration string; include actual generated-artifact or process behavior where that is the failure mode.

| Task | Size | Dependencies | Status / exit evidence |
|---|---|---|---|
| 1. Track A bootstrap and env state | M | Design approval; baseline sync done | In progress: native guard/RAUC tests, host seed generation and generated-default budget, strict legacy export/preflight and manual evidence recorder; image/Pi proof, actual adoption and final runtime budget pending |
| 2. Slot-contained Linux | M | 1 | Pending; distinct ROOT kernels, checked counters, terminal halt |
| 3. Every-boot health and watchdog | M | 2 | Pending; normal boot refresh, kernel/app/hang reset, bounded fallback |
| 4. Shared trust and packages | M | 1 | In progress: shared policy/entry/bundle verification, generated keyring, verity bundles and real RAUC 1.15.2 / guarded fw-tools tests ([evidence](../../evidence/os-updates/signed-packages.md)); production keys and on-box install pending |
| 5. Host staging and admission | M | 4 | In progress: admission, owned resumable staging and space checks with host tests ([evidence](../../evidence/os-updates/installer.md)); hardware measurements pending |
| 6. RAUC transaction and durable guard | M | 2–5 | In progress: worker, journal, RAUC transaction identity, independent slot check, guard and hostd refusals ([evidence](../../evidence/os-updates/installer.md)); app guard-v1 and spare-card kill/power cases pending |
| 7. Activation, reconciliation and app safety | M | 3, 6 | In progress: intent-first Restart, Discard, trial confirmation, rollback/interrupted-activation reconciliation, factory reset mid-trial and app promotion with host tests ([evidence](../../evidence/os-updates/activation.md)); Pi OS×app matrix and migrated-database fallback tests pending |
| 8. Settings end-to-end online update | M | 5–7, paired app capability | In progress: host os_* actions and the app's EpilykosOS card, routes and guard-v1 (ashipaek0/Epilykos#215) with request/DOM tests; Pi rehearsal pending |
| 9. Authenticated offline imports | M | 4–8 | Pending; network-blocked upload/USB installation |
| 10. Track B recovery and boot-chain updater | M per sub-PR | Its own feasibility ADR; reuse Track A interfaces | Pending separately; does not gate A1/A2 |
| 11. Track A publication and acceptance | M per sub-PR | 1–8 for online; add 9 for offline | In progress: gated protected publication (package release, then pointer) and the full §12 acceptance recorder ([runbook](../../evidence/os-updates/release-and-acceptance.md)); production keys, spare-card matrix and soak pending |

These are review boundaries, not permission to put all files of a subsystem in one oversized PR. Split a task if its diff exceeds a focused review; preserve the interfaces below. Checkpoint after Tasks 1–3, 4–7 and 8–11.

### Task 1: Track A bootstrap and existing-environment state

**Files:** create `plans/decisions/pi3-update-bootstrap.md`, `tools/bootstrap_export.py`, `tools/test_bootstrap_adoption.py`, `tools/test_platform_env.py`, `tools/hardware/os_slot_acceptance.py`, `tools/hardware/device-config.example.json`, `evidence/os-updates/pi3-bootstrap.md`; inspect current board files. No Track B selector/recovery payload implementation belongs here.

**Interfaces:** produce `boot_protocol=2`, platform metadata and bounded env variables `epk_os_floor`, `epk_os_pending`/target/from/phase, confirmed identity, `epk_trust_policy`, `epk_trust_policy_sig` and non-wrapping `epk_state_generation`. Existing redundant 16 KiB env copies/offsets remain. Policy schema 1 is at most 6 KiB before base64; leave at least 2 KiB free after all serialized variables. Critical-state selection runs before loader saveenv, preserves the greatest valid generation/epoch/floor and refuses conflicts; critical commits force/read back replication to both copies before acknowledgement. Normal RAUC counters preserve critical generation. All runtime writers re-read under one common lock. No extra raw region, recovery credential or independent selector is required for Track A.

- [x] Main-to-dev baseline sync is complete in #83; inspect refreshed refs before coding and preserve it.
- [ ] Add failing native-env/adoption tests: actual capacity, torn first/second critical write, no-op replication, concurrent writers, unequal/conflicting generations, single-copy loss versus both-copy corruption, non-wrapping generation and default-zero refusal. Specifically acknowledge epoch N+1/floor F+1, corrupt only the newest copy and boot the retained slot: counter saveenv and external install must never revive N/F. Also test same-channel app adoption, DB-only restore and factory reset preservation.
- [ ] Wire the same generation-aware adapter/patch into target fw_printenv/fw_setenv as used by RAUC/health/machine-ID/policy paths; native active-copy selection must not bypass it. Exercise real mark-good/mark-active and the common lock, not only a Python state mock.
- [ ] Specify seeded bootstrap env/public policy, source-host authenticated export and explicit spare-card adoption. Test normal first flash separately from lost state; never silently initialize corrupt state to zero. No recovery enrollment/adoption-ticket service is required for Track A.
- [ ] Add a slot acceptance runner recording image/card/board identity and explicit manual power-cut checkpoints/results; no rig or serial dependency. Run `python3 tools/test_platform_env.py` and `python3 tools/test_bootstrap_adoption.py`; commit the ABI/procedure and measured env budget. Track B's feasibility ADR remains in Task 10.

### Task 2: Build and load complete Linux slots

In progress: the artifact validator, read-only packed-ext4 checker and checked
protocol-2 script have host/sandbox coverage, and a development build profile
(`EPILYKOS_BOOT_PROTOCOL=2`) wires slot Linux, seeded environment, the slot
script and the BOOT trust root into the image with a whole-card check; see
[evidence and remaining integration](../../evidence/os-updates/slot-linux.md).
Shipping images still select protocol 1. Keep Task 2 unchecked until the full
protocol-2 image build and real Pi acceptance pass.

**Files:** modify `os/board/epilykos/raspberrypi3/{post-build.sh,post-image.sh,boot.cmd,uboot.fragment,genimage.cfg}`, `os/configs/epilykos_raspberrypi3_defconfig`, `.github/workflows/os-build.yml`; add the pinned U-Boot env-import/guard change under board package patches and create `tools/test_slot_linux.py`, `tools/check_slot_linux.py`. Exercise the guard on actual native env records; a Linux-only repair cannot undo a stale loader saveenv.

**Interfaces:** immutable `/usr/lib/epilykos/platform.json` includes `schema=1`, `board=raspberrypi3`, `arch=arm64`, `slot_layout=pi3-mbr-v1`, `boot_protocol=2`, channel, OS release/sequence/source hash, kernel release and host API version. Paths for slot Image/DTB are fixed in the design. Boot slot names remain A/B and existing PARTUUIDs remain unchanged.

- [ ] Add failing tests `test_slot_artifacts_match_modules`, `test_selected_slot_supplies_both_linux_files`, `test_missing_dtb_never_reuses_prior_ram`, `test_counter_save_failure_halts`, `test_trial_started_persists_before_launch`; fixture distinct release kernels and invalid loads. Assert three failed load/boot attempts, no shared-kernel fallback and a non-looping diagnostic halt when no slot is eligible.
- [ ] Stage kernel/DTB before rootfs packing; enable and exercise U-Boot ext4 loading; save counter before launch, check each result and route failed loads deterministically. Keep required pre-U-Boot firmware artifacts separate.
- [ ] Update image structural checks for both ROOTs, module ABI/hashes/capacity and seeded valid redundant env, replacing the old blank-env check. Verify required immutable BOOT/root-public-key files without requiring shared Linux Image/DTB as the normal boot source.
- [ ] Run `python3 tools/test_slot_linux.py`, `make test contracts`, full image build and the actual generated-slot checker. Then boot A with one kernel and B with another on the Pi, recording `/proc/cmdline`, `uname -r`, module loading and DATA/settings identity.
- [ ] Commit the slice; retain old card and publish experimental artifacts only under accurate support labels until acceptance.

### Task 3: Every-boot health and watchdog ownership

In progress: the supervisor, unit graph and watchdog chain are implemented
for protocol-2 cards with host coverage; see
[evidence and pending hardware cases](../../evidence/os-updates/boot-health.md).
Keep Task 3 unchecked until the spare-card hang/deadline/fallback cases pass.

**Files:** replace internals of `rootfs-overlay/usr/bin/epilykos-mark-good`; modify its service/preset, `boot.cmd`, `linux.fragment`, `uboot.fragment`; create `rootfs-overlay/usr/lib/epilykos/python/epilykos_boot_health.py`, a systemd watchdog drop-in, `tools/test_boot_health.py` and hardware cases. All overlay paths are below `os/board/epilykos/raspberrypi3/`.

**Interfaces:** supervisor starts independently of DATA/app readiness and uses one monotonic 1200-second deadline from userspace startup. Every healthy boot refreshes attempts: ordinary boots call RAUC mark-good; a pending trial delegates to Task 7's coordinator, which records confirmation intent before its mark-good. Do not mark-good twice/out of order. The protocol-2 loader arms/services the hardware watchdog before kernel launch and hands it through to systemd. Target runtime watchdog 10 s, supervisor WatchdogSec 15 s/heartbeat at most 5 s, with bounded subprocesses/shutdown reset. Prove the actual short hardware maximum/handoff on Pi.

- [ ] Add cases for six consecutive healthy confirmed-slot reboots retaining/resetting counters, slow OCI import, stalled migration/deadline, supervisor hang with live PID1, pre-PID1 kernel hang, unavailable DATA, lost env, exhausted-slot halt and no Internet/inverter. Assert three candidate attempts without counter refill or falsely promoting an ordinary reboot.
- [ ] Implement early supervision, bounded reboot/reset and DATA-safe failure/halt. Preserve RAUC as normal runtime slot-state writer and serialize environment updates; fail safely on state-write errors. No independent recovery UI/selector is a dependency.
- [ ] Run `python3 tools/test_boot_health.py`; verify systemd dependency graph has no after-app deadline or recovery/mark-good cycle. Run real Pi hang/deadline/A-B fallback tests; process-kill-only tests do not prove kernel/early reset coverage.
- [ ] Commit with measured handoff coverage and remaining physical limitations recorded.

### Task 4: Native bundles and shared delegated authorization

In progress: implemented with host, real RAUC and native env coverage; see
[evidence](../../evidence/os-updates/signed-packages.md) and the custodian
procedure in `keys/os-release/README.md`. Generating production keys stays
the maintainer's step.

**Files:** modify RAUC `system.conf`, defconfig/kernel requirements and board hooks; create `tools/{build_os_bundle.py,test_os_bundle.py,verify_os_release.py,test_verify_os_release.py,test_os_trust_policy.py}`, release/policy schemas, `keys/os-release/README.md`, test-only CA/delegation fixtures and pinned offline index-root public key provisioning. Reuse Task 1 env state adapter. Production secrets stay outside the repo.

**Interfaces:** `verify_policy(raw: bytes, signature: bytes, root_pubkey: bytes, current: dict) -> dict`; `verify_os_release(entry_bytes: bytes, signature: bytes, platform: dict, policy: dict) -> dict`. Policies have channel/board, schema 1, monotonic epoch, allowed index keys, RAUC CA certificates and signing-leaf SPKI fingerprints. Validate the actual verified CMS signer, not any certificate present in its chain. Same-epoch policy must be identical. Both slots load the shared env policy and generate `/run/rauc/epilykos-keyring.pem`; no stale slot-keyring fallback. Signed entry and native `[meta.epilykos]` agree, including trust epoch; exact entry/signature accompany every bundle online/offline. `build_os_bundle` derives bundle and flash rootfs from the same bytes.

- [ ] Add real RAUC fixtures for valid/unsigned/tampered/wrong-CA packages, wrong delegated index key/channel/board/protocol, unexpected image/handler, absent offline authorization, mismatched signed pair, over-capacity rootfs, replay and unknown schemas.
- [ ] Test policy update at epoch N+1, then boot/run the retained epoch-N OS implementation and reject a higher-sequence forgery by revoked RAUC/index keys. Cover failed OS candidate after trust advancement, factory reset, stale/equal-mutated policy, env corruption, and interrupted env/policy writes. OS floor never acts as key revocation.
- [ ] Implement verity bundles, effective runtime CA keyring, shared policy/entry verification, signed metadata, `activate-installed=false` and required native/kernel features. Reload RAUC under exclusion after a policy change; no live transaction loses its writer. Run real 1.15.2 tests, not text-only mocks.
- [ ] Run `python3 tools/test_os_bundle.py`, `python3 tools/test_verify_os_release.py`, `python3 tools/test_os_trust_policy.py`; prove rejection before slot writes. Document signing-time validation's expiry trade-off and inability to discover undelivered offline revocations.
- [ ] Deliver the custodian procedure: offline CA/index roots sign certificates/delegations; protected CI holds only channel-scoped delegated keys. Root replacement requires reflash/Track B; ordinary signing needs no per-image approval. Generating production keys remains the maintainer's explicit step.

### Task 5: Owned staging, storage accounting and transfer protocol

**Files:** create overlay Python `epilykos_os_release.py`, `epilykos_storage.py`, `epilykos_os_upload.py`; new owned upload socket/service or hostd integration; create `tools/{test_os_staging.py,test_storage_admission.py}`. Reuse app maintenance storage policy instead of inventing different thresholds.

**Interfaces:** `admit_os(entry: dict, platform: dict) -> dict` returns required/available bytes and blockers. `stage_online(job_id: str, entry: dict) -> dict` yields only an owned verified package identity. `begin_upload(entry: dict, request_id: str) -> dict` yields opaque transfer ID/token and exact raw-stream length budget. Public errors never expose private paths/tokens.

- [ ] Add tests for peak headroom including retained bundle and future OCI expansion, unknown length/hash/size, monitoring growth/ENOSPC, disconnect, symlink/hardlink/path substitution, token replay, replaced USB file and concurrent admission. Cover HTTP Range ignored/changed ETag/wrong Content-Range: never append incompatible content. No bundle appears in `/tmp`; RAM stays bounded.
- [ ] Implement 1 MiB DATA streaming/owned transfer tokens; enforce 8 KiB entry/index, 6 KiB root-signed policy, 64 KiB other metadata and 16 KiB serialized control headers. Larger policy transfer uses the bounded stream separately. Hash complete resumed files; pin file identity through RAUC handoff and retain candidates through ready/trial until safe terminal cleanup.
- [ ] Run `python3 tools/test_os_staging.py` and `python3 tools/test_storage_admission.py`; measure disk allocations and peak RSS on bounded large fixtures. Cleanup removes only owned terminal orphan files, not journal-referenced candidates or protected assets.
- [ ] Commit with explicit disk-budget examples for actual bundle/rootfs/OCI sizes; do not use guessed compression ratios.

### Task 6: Durable RAUC installation and maintenance guards

**Files:** create overlay Python `epilykos_os_update.py`, `epilykos_rauc.py`, worker executable/service; modify `epilykos_update.py`, `epilykos-hostd`, `epilykos_maintenance.py`, reset admission and service presets; app companion changes in `modules/maintenanceLock.js` and existing maintenance entry points; tests `tools/test_os_update.py`, `tools/test_os_guard.py`, app `test/os-maintenance-guard.test.js`.

**Interfaces:** OS backend `status() -> dict`, `check() -> dict`, `install(sequence: int, sha256: str, request_id: str) -> dict`, `recover() -> None`; host dispatch maps `os_status/os_check/os_install`, with job-bound cancel/restart/discard/dismiss added by Tasks 7–8. Existing app API remains and `update_apply(target='os')` continues refusing. Guard schema/mount/guard-v1 label/env are exactly design §7.4; private state stays unmounted. Store RAUC transaction UUID, D-Bus unique owner and systemd invocation before installation.

- [ ] Write failing integration cases killing hostd, worker and RAUC separately during a write. Start a competing restore/app update/factory reset and prove it cannot proceed while the RAUC operation remains live, including after flock owner death.
- [ ] Implement systemd jobs/progress, private journals, source-slot confirmed/healthy admission and exact request-ID idempotency. A replacement idle RAUC service means interrupted/unknown; prove old cgroup writers gone and target invalidated before clearing the active guard. Refuse arbitrary paths, automatic OS mutation and conflicting/pending jobs; monitoring continues.
- [ ] Commit `pending` at ready before releasing the live lock, keep it through restart/trial and reconstruct it before app startup. Test atomic guard replacement through the read-only directory mount, missing/unknown guard fail-closed, blocked restore/app/reset/restart changes throughout ready, and allowed pending backups/snapshots excluding Restart until their leases close. Old guard-unaware apps cannot enable installation.
- [ ] Run `python3 tools/test_os_update.py`, `python3 tools/test_os_guard.py`, companion app fixtures and affected suites. Confirm no synchronous request waits for installation and browser close does not abort/duplicate it.
- [ ] Commit paired OS/app PRs with a backward-compatible capability rollout; expose install only after both halves are present.

### Task 7: Explicit activation, promotion and application retention

**Files:** create overlay Python `epilykos_platform_state.py`, `epilykos_app_selection.py`; modify OS updater/health gate, `epilykos-run`, `epilykos-image-load`, app updater bookkeeping, factory-reset handling; tests `tools/{test_os_reconcile.py,test_app_selection.py,test_platform_state.py}` and hardware reboot cases.

**Interfaces:** `select_app(platform: dict, promoted: dict|None, rollback_platform: dict) -> dict`; `restart(job_id: str) -> dict`; `discard(job_id: str) -> dict`; `reconcile_boot() -> dict`; `confirm_candidate(job_id: str) -> dict`. Reuse bounded existing-env intent/floor/policy from Task 1; no new raw region or recovery credential. Persist activation intent before mark-active, confirmation intent before mark-good, then commit floor/identity/promotion phase before finalizing app DATA/result state.

- [ ] Add cuts before/after journal/env intent, mark-active/readback, loader trial marker, mark-good, floor commit, app promotion and final result. Cover malformed/missing env, factory reset, generic reboot and discard. Source boot before a candidate attempt is not falsely called rollback; failed trials do not raise floors, ordinary healthy boot only refreshes its counter, and retained fallback stays bootable.
- [ ] Test promoted app newer/older/equal to bundled, mixed channel, missing image, both host APIs/guard-v1 and trial failure. A failing bundled app remains tentative; OS confirmation finalizes app/floor via recoverable intent, never an assumed cross-filesystem atomic write. Actually migrate then read/write with the confirmed fallback app, including dev pairs; a db-compat label alone is not proof.
- [ ] Implement intent-first activation, current-policy revalidation, exact-target readback and early idempotent promotion/rollback reconciliation. Reconstruct pending state after a physical factory-reset request erases DATA while preserving OS floor/trust/identity; fresh-DATA app selection uses the signed baseline. Requested generic restart cannot silently activate; spontaneous ready power cuts stay on the source slot.
- [ ] Run focused tests and real Pi OS-success/failure × app-success/failure cases. Compare DATA markers/settings/DB and inspect actual selected image IDs, not UI text alone.
- [ ] Commit with compatibility metadata and the explicit no-destructive-migration release rule.

### Task 8: Settings online update journey

**Files (app):** `routes/updates.js`, `modules/updates.js`, `modules/hostActions.js`, `public/js/updates-settings.js`, result/notification rendering; add `test/os-updates-http.test.js`, `test/os-updates-ui.test.js`. OS adds the design's job-bound os_* dispatch to `epilykos-hostd`; document it in `docs/maintenance/os-update-actions.md`.

**Interfaces:** design §10 is canonical: `GET /api/updates/status` maps to `os_status`; POST `/api/updates/os/check|install|cancel|restart|discard|dismiss` map to `os_check|os_install|os_cancel|os_restart|os_discard|os_dismiss`. Install passes `{sequence,sha256,request_id}`; restart/discard require password and job ID. All routes are session/CSRF protected. Preserve boolean `host.os.available` and legacy `latest_version`, add detailed `latest/job/last_result`; app `applying` retains its meaning. Existing update_apply(target=os) refuses; no parallel /apply or /os/reboot implementation.

- [ ] Add request and DOM tests: wrong password/session/origin rejected; app update behavior unchanged; OS installation longer than 15 minutes; browser refresh/disconnect; stale release selection; reboot-required state; resumed confirmed/rolled-back result; dismissal bound to job; unsupported old host; app auto-install cannot select OS.
- [ ] Implement capability-driven review/install/progress/restart/result states. Visible active polling is 2 seconds with connection backoff; no invented ETA, automatic activation on reconnect, or app-style restart assumption during staging.
- [ ] Run app tests and end-to-end app/host disposable integration. Rehearse the complete online flow on Pi, including authenticated restart and deliberately unhealthy candidate; monitoring remains active until restart.
- [ ] Commit the paired UI/API slice, retaining old-host and Docker/Compose fallbacks.

### Task 9: Track A2 online-equivalent upload and USB import

**Files:** app upload/import routes and UI in Task 8 files; overlay upload adapter and USB enumeration/import module; `tools/test_os_offline.py`, app `test/os-update-upload.test.js`.

**Interfaces:** bundle plus exact signed authorization/signature, and offline-root-signed policy if needed. Same env epoch/revocation/floor and native verifier as online; `.raucb` alone is refused. Use bounded metadata admission/one-use transfer token and separate raw-byte host stream, not a 16 KiB JSON payload or arbitrary host path. USB files use enumerated opaque IDs and read-only `nodev,nosuid,noexec` mounts. Insertion discovers only.

- [ ] Add network-blocked valid install plus missing authorization, unknown/revoked index or RAUC signer, stale/equal-mutated policy, wrong board/channel/sequence, truncated/removable/replaced file, disconnect, oversized input and certificate signing-time trade-off cases. Reject a revoked signer's higher-sequence forgery after booting the retained older slot. No insertion or failed import activates anything.
- [ ] Implement authenticated bounded upload through the separate Unix stream, and owned USB copy; one verifier and one authorization model for all three delivery paths.
- [ ] Run `python3 tools/test_os_offline.py`, app upload fixtures, then real Pi offline install/reboot/rollback. Test certificate validity/rotation offline under the approved policy.
- [ ] Commit with documented package naming and a usable import failure/retry flow.

### Task 10: Track B independent recovery and boot-chain updates

**Files:** a separate Track B feasibility/storage/authorization ADR, experimental selector, `os/recovery/`, dedicated signed boot-chain tools/worker and authenticated repair UI; `tools/test_recovery.py`, `tools/test_boot_chain_update.py`, `tools/hardware/boot_chain_acceptance.py` and evidence. Split into focused PRs. These files are not prerequisites of Tasks 1–9/11.

**Interfaces:** first prove the protected pre-candidate boundary, bad-loader reset/selection, 128 MiB BOOT storage and DATA-independent authorization in this track's own ADR. Reuse Track A's verified env/trust interfaces where feasible; any new layout/control format requires separate approval. Recovery/repair must not depend on normal ROOT/DATA/Podman. Replacing the fixed bootstrap or offline root is forbidden to ordinary slot packages.

- [ ] Prove the pre-U-Boot recovery boundary and capacity, then specify per-device credential provisioning/loss/reset and independent repair. Test both ROOTs/DATA unusable, authorization failure, native signature plus authorization/policy rejection and interrupted repair preserving DATA. If equal-sequence repair is introduced, restrict it to the exact recorded confirmed identity without lowering floors; no general downgrade override.
- [ ] Implement authenticated repair and trust rotation with rollback-safe recovery payload handling. Distinguish shared-DATA failure from two bad normal slots; never automatically format or start DB writes on unusable DATA.
- [ ] Implement the separate early boot-chain updater only inside the proven recovery boundary. Test truncated/corrupt/valid-but-hanging loader and power loss at write/select/confirm transitions. No ordinary RAUC rootfs bundle may mutate shared boot artifacts.
- [ ] Run host tests and actual Pi cases. Manual D3 power-cut/restore tests are valid physical evidence; needing an extra manual cycle to rescue a powered-on hanging loader is not automatic recovery. Missing equipment/emulator-only cases remain pending without blocking Track A.
- [ ] Commit each focused PR with evidence and exact protected components. Do not declare full v1 ready if this required workstream is still unproven.

### Task 11: Track A release gates and measured platform acceptance

**Files:** `.github/workflows/os-build.yml`, new protected OS signing/publishing workflow if separation is needed, `tools/image_release_policy.py`, `tools/test_release_gates.py`, package verification tools, `evidence/os-updates/`, hardware runner; update contract implementation/evidence statuses accurately and run rendering/checks.

**Interfaces:** one rootfs produces `.img.zst` and `.raucb`; publish immutable per-package authorization/signature, root-signed channel policy, hashes/provenance and Imager list. The signed discovery pointer advances last. Protected channel-specific CI uses delegated keys, not root private keys. A1 online needs Tasks 1–8 and this task's online evidence; A2 offline adds Task 9/evidence. Task 10 never gates these releases; full Track B recovery is not claimed.

- [ ] Add failing publication cases: missing/tampered bundle/authorization/policy, wrong/revoked signer, stale epoch, unauthorized source/channel, reused sequence, wrong arch, over-capacity rootfs/env, missing guard-aware compatible app, root/private-key leakage or PR secret access, incomplete upload, broken loader/health configuration and failed DB rollback fixture.
- [ ] Implement protected signer/source gating, serialized sequence allocation, immutable-asset collision refusal and last-step signed-pointer publication. Re-run native bundle/slot checks on downloaded output artifacts. Keep current QEMU diagnostic.
- [ ] On the spare card record six healthy counter-refresh reboots, good update, three-attempt bad kernel/DTB/app/hung-kernel fallback, supervisor hang, shared-DATA failure and both-ineligible diagnostic halt. Test worker death with RAUC alive, pending backups/restart/discard, wrong/revoked signatures after OS fallback, env loss and safe app/DB/factory-reset behavior. No independent recovery UI or broken-early-loader rescue is claimed for Track A.
- [ ] Use manual D3 power cuts at downloading, native writes, ready, before/after env activation intent, mark-active, trial and confirmation/floor commit. Capture actual observed checkpoint and mark unobserved transitions pending, not passed. No power-control rig/serial cable is required; never test on the working card. Task 9 adds offline install evidence, not Track B offline repair.
- [ ] Measure actual download/install duration, worker and RAUC peak RSS, headroom, monitoring latency and post-reboot outage with representative approximately 5.9 GB DATA and a 72-hour soak. Establish limits from that evidence before enabling production publication.
- [ ] Run `make test contracts`, app required tests, full image/package workflow and `python3 tools/hardware/os_slot_acceptance.py --device-config DEVICE.json`. Preserve machine-readable identities/results/logs/digests. Publish automatically after existing maintainer merge authorization and meaningful deterministic gates. Retain QEMU as diagnostic until calibrated; no routine manual per-image approval.
- [ ] Update owning Track A contract/evidence statuses (C-BOOT-002/003, C-UPDATE-001, C-HOST-001 and new OS policy/floor contract); leave C-BOOT-004 and independent recovery pending under Task 10. Archive each completed track's work honestly rather than requiring Track B before releasing A.

## Delivery checkpoints and maintainer actions

- **A1 foundation (1–3):** spare-card bootstrap, full Linux slots, every-boot health and measured watchdog handoff. No Track B selector/credential prerequisite.
- **A1 transaction (4–7):** shared revocation, signed bundle plus authorization, guarded crash-safe install/activation and app compatibility.
- **A1 release (8 + 11 online):** Settings online installation/rollback accepted on spare hardware, including manual D3 cuts and representative soak, then automatic publication.
- **A2 release (9 + 11 offline):** equivalent authenticated upload/USB acceptance; no bundle-only bypass.
- **B (10):** independent feasibility, recovery and early updater evidence. Separate from A1/A2 release.

D1–D3 are settled. The maintainer generates offline roots/delegations and
provisions protected CI delegated keys, then participates in the named manual
spare-card test checkpoints. No rig, serial cable or independent recovery
credential is requested for Track A. Missing Track A evidence blocks its
release; missing Track B evidence remains visible on that track. This document
revision is not authorization to implement, merge, publish or alter hardware.
