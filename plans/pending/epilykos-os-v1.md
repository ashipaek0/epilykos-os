# EpilykosOS v1 implementation plan

Status: proposed execution sequence for the approved v1 scope; implementation has not started under this plan.

Date: 2026-10-17. Integration target: `dev` in `ashipaek0/epilykos-os` and, where needed, `ashipaek0/Epilykos`. Stable source remains `main`. Changes use focused PRs authored as `ashipaek0`; the existing maintainer merge decision authorizes release. This plan does not authorize merging or publishing on the user's behalf.

Approved requirements: [locked scope and policies](../decisions/epilykos-os-v1-scope.md). Those decisions are not reopened by this plan. Implementation details must satisfy them; a demonstrated hardware limitation must be reported, not hidden by weakening a requirement.

## Outcome and critical dependency

Deliver a Pi 3B appliance with recoverable signed OS/boot-chain updates, reliable production-size data maintenance, offline application updates and automatic release publication governed by meaningful automated checks.

The largest uncertainty is recovery *before a broken U-Boot starts*. An atomic write or Linux watchdog alone does not prove it. Establish the protected bootstrap boundary, candidate selection and reset/fallback coverage before building the final boot-chain updater or OS installation layout. Independent Linux recovery must also fit without assuming a fifth MBR partition or repartitioning deployed cards.

Only initial platform acceptance and targeted changes to boot/storage/recovery need refreshed physical evidence. Ordinary published images do not acquire a manual flash/approval gate. Automated physical testing is useful but no test Pi, switched-power rig or remote access is currently available to this workspace.

## Delivery sequence

1. Repair the build, reconcile contracts and make automated status reporting accurate.
2. Prove the Pi 3B boot-chain/recovery design. Independently implement storage/data safety while physical evidence is pending.
3. Implement slot-contained Linux artifacts, bounded boot failure handling, independent recovery and the dedicated boot-chain updater.
4. Finish signed online/offline OS and application installation, diagnostics and read-only runtime operation.
5. Collect the initial Pi 3B acceptance baseline and complete automatic stable publication. Add Pi 4, then Pi 5, without blocking Pi 3B.

Each numbered work item below is a candidate focused PR, not a promise that one PR can contain an entire subsystem. Split an item further if it exceeds roughly 3–8 files or a focused review. Keep the host API, UI and package formats backward compatible across paired app/OS PRs, and do not expose an action until the host reports the capability.

## Common verification

Existing commands, run from their respective repository roots:

- OS: `make test`, `make contracts`; regenerate contract output with `make render` when YAML changes.
- Application: `npm test` using Node 22; CI setup is `npm ci --no-audit --no-fund`.
- Full Pi image: `EPILYKOS_CHANNEL=dev sh os/build.sh raspberrypi3`, normally through the existing `os-build.yml` workflow. Do not rebuild for docs-only changes.
- CI dispatch when implementation is ready: `gh workflow run os-build.yml --repo ashipaek0/epilykos-os --ref dev -f board=raspberrypi3 -f channel=dev`; inspect the actual job/artifact results, not just dispatch success.
- Every change: `git diff --check`, relevant focused tests, then the affected repository's required regression checks. Do not repeat unchanged suites without a reason.

**Planned verification commands** below do not yet exist. Add them with the owning feature and register required ones in CI/`make test`. Until then, they are specification targets, not tests reported as passing. Hardware runners must record board/media/artifact identity and explicit PASS/FAIL/PENDING evidence. Mocks, emulators and process termination must never be labeled physical power-cut evidence.

## Work items

### B01 — repair image assembly (M)

Dependencies: none. Files: `os/board/epilykos/raspberrypi3/post-build.sh`, any required narrowly scoped Buildroot hook, `.github/workflows/os-build.yml`, focused regression in `tools/`.

- Reproduce the permission-denied cleanup of staged `/etc/bluetooth` configuration. Inspect Buildroot's filesystem/fakeroot lifecycle; do not assume the apparent chmod is the complete cause or apply a broad privileged cleanup workaround.
- Produce an image successfully with the required runtime permissions preserved, including a repeat build that exercises the failing lifecycle.
- Verification: existing full-image build plus planned `python3 tools/test_image_permissions.py`; retain the generated image/hash and original failure log.

### B02 — align contracts with the approved scope (S)

Dependencies: none. Files: `contracts/epilykos-os-contracts.yaml`, `contracts/EPILYKOS-OS-CONTRACTS.md`, affected narrative/evidence references and validator fixtures.

- Record all locked requirements/deferrals; remove superseded counter-reset/reflash-only behavior, shared Linux artifacts and stale provisioning/access claims.
- Keep implementation and evidence statuses pending where applicable. Do not introduce routine manual image approval through contract wording.
- Verification: `make render`, `make contracts`, `make test`; review generated requirements against all 22 approved decision IDs.

### B03 — make CI results meaningful (M)

Dependencies: B01. Files: `.github/workflows/os-build.yml`, `.github/workflows/os-build-check.yml`, `tools/` artifact checker and tests.

- Real build/structure/configuration failures block publication; a missing artifact suppresses dependent boot checks instead of reporting a fictitious boot failure.
- Keep the current QEMU path explicitly diagnostic until calibrated with known-good and deliberately broken images. Preserve logs and identify emulator limitations. Do not make an unexplained green/ignored failure the release criterion.
- Verification: planned `python3 tools/test_image_validation.py` and `python3 tools/test_release_gates.py`, existing workflow checks, deliberately damaged artifact/config fixtures.

### D01 — prove early boot recovery and storage feasibility (M)

Dependencies: B01. Files: new ADR under `plans/decisions/`, feasibility scripts under `tools/hardware/`, evidence under `evidence/`; candidate board files only in an explicitly experimental branch.

- Inspect pinned upstream support and demonstrate selection, reset and fallback before/during a corrupt or hanging candidate U-Boot. Identify the earliest components that must remain protected; test power loss across selection/confirmation writes.
- Map recovery payload, redundant boot artifacts, environment and credential storage onto the MBR layout. Assess capacity and safe adoption on existing cards without assuming a partition migration. Define compatibility with both root slots and recovery.
- Verification: planned `python3 tools/hardware/boot_chain_acceptance.py --device-config DEVICE.json`; requires a real Pi and power control for physical cases. Publish the ADR and measured results. Without the hardware result, mark feasibility pending and continue independent work; do not claim this dependency passed.

### D02 — specify recovery authorization and package trust (S)

Dependencies: D01's storage design. Files: recovery/trust ADRs under `plans/decisions/`, documented host API/package schema.

- Define per-device recovery credential provisioning, storage outside DATA, authentication, physical reset and lifecycle. No shared default credential, DATA dependency or unauthenticated repair endpoint.
- Specify offline certificate-time behavior, signing/rotation/revocation and recovery trust updates; define OS version/replay handling separately from application sequence protection. A clock or missing-state error must not silently disable verification.
- Verification: ADR threat/failure review with concrete missing-DATA, wrong-clock, lost-credential, expired/replaced-signer and old-recovery-image cases; executable negative tests are delivered in the owning features.

### S01 — coordinated storage admission (M)

Dependencies: B02. Files: host Python storage module/API, `epilykos_update.py`, app maintenance interface, `routes/system.js`, storage UI and tests (split backend/UI if necessary).

- Expose actual usable DATA space and enforce warning `max(20%, 2 GiB)` and maintenance headroom `max(10%, 1 GiB)` with per-operation peak allocation estimates.
- Reserve concurrent maintenance budgets, bound streamed/expanded input, recheck transitions and handle ENOSPC; protect retained snapshots, live data and rollback images. Identify safe orphan cleanup explicitly.
- Verification: planned `python3 tools/test_storage_admission.py`, app storage fixtures through `npm test`, low-space/concurrency/growth fault cases.

### S02 — consistent large backup downloads (M)

Dependencies: S01. Files in app: `modules/backup.js`, database integration/worker, `modules/maintenanceLock.js`, `server.js`, focused tests.

- Generate transactionally consistent SQLite backups with WAL accounted for; move long capture/check/copy work off the dashboard event loop and stream the download with bounded memory.
- Coordinate snapshot creation and concurrent download lifetimes so one job cannot overwrite/delete another's file. Bound staging and clean abandoned files safely.
- Verification: `npm test` with concurrent writes/downloads, checksum/content comparison and interrupted clients; planned `node test/hardware/large-data-maintenance.js --case backup --database SAMPLE.db` on a production-size disposable copy.

### S03 — large restore upload and validation (M)

Dependencies: S01. Files in app: `server.js`, dedicated staging/validation module/worker, restore UI, tests.

- Replace database restore's 50 MiB/RAM staging path with bounded streaming into private DATA staging. Keep small import limits independent; do not expand RAM-backed `/tmp` to database size.
- Check integrity, expected schema, supported migrations and peak disk use before replacement. Display progress and the monitoring interruption boundary; reject invalid/oversized/incompatible content without touching the active DB.
- Verification: `npm test`, planned `node test/hardware/large-data-maintenance.js --case upload --database SAMPLE.db`, slow/aborted upload, corrupt DB and bounded-memory/disk-full cases.

### S04 — interruption-safe restore transaction (M)

Dependencies: S02, S03. Files in app: restore transaction module, `modules/backup.js`, database startup/rebind integration and transaction tests.

- Quiesce every database user, handle WAL/SHM consistently, record durable transition state and retain the original until replacement checks/migrations and health succeed.
- On process death/reboot, select a complete recoverable state; never open a partially copied database or apply unrelated sidecars. Define failure recovery when the candidate migration is irreversible.
- Verification: `npm test` with termination at every transition and planned `node test/hardware/large-data-maintenance.js --case restore --database SAMPLE.db`; physical cut matrix in H01.

### S05 — compressed daily snapshots (M)

Dependencies: S02, S04. Files in app: `modules/backup.js`, compression/worker helper, snapshot UI and tests.

- Stream gzip, publish durably before pruning, retain the two newest successful snapshots, and restore both gzip and legacy `.db` files through the validated restore path.
- Coalesce missed days, wait for health/maintenance availability, bound retries and warn on overdue/failed snapshots. Enforce decompression and scratch-space bounds without assuming compression helps.
- Verification: `npm test`, planned large-data runner `--case snapshot`; failed compression, clock changes, corrupt archive and publish/prune interruptions.

### K01 — slot-contained Linux artifacts (M)

Dependencies: D01 layout. Files: board `boot.cmd`, `uboot.fragment`, `post-build.sh`, `genimage.cfg`, image validation/extraction tests.

- Install matching Linux Image/DTB/modules inside each rootfs; load from the selected ext4 slot with required U-Boot filesystem support enabled.
- Keep root selection, RAUC compatible identity and DATA persistence consistent. Update emulator extraction/structural checks to the new location; retain an explicit adoption/compatibility path for existing images.
- Verification: planned `python3 tools/test_slot_artifacts.py`, full image validation and real A/B boots with distinguishable Linux artifacts under H01.

### K02 — bounded boot-health failure handling (M)

Dependencies: K01 and D01 reset/watchdog coverage. Files: boot script, `epilykos-mark-good`, related systemd units/watchdog setup, tests.

- Apply one 20-minute deadline from userspace startup, including image load/checks/migrations; retain three attempts and restart automatically on expiry/hang.
- Confirm local health without network/inverter dependence. Keep normal-health, DATA recovery and independent recovery states distinct so recovery does not accidentally confirm a failed normal slot.
- Verification: planned `python3 tools/test_boot_health.py`, deadline/frozen-service/restart fault tests and real watchdog/slot fallback under H01.

### K03 — staged dedicated boot-chain updater (M)

Dependencies: D01, D02. Files: dedicated host boot-chain module/service, authenticated package schema, board-specific storage/selection handlers and tests.

- Stage and authenticate a candidate without changing the known-good chain; validate compatibility with both root slots and the independent recovery payload.
- Implement durable state, exclusive ownership of mutable boot artifacts and storage bounds. Preserve the earliest recovery/bootstrap boundary from D01; enumerate components that cannot safely be updated in place.
- Verification: planned `python3 tools/test_boot_chain_update.py --case staging`, malformed/wrong-board/incompatible candidates and interrupted writes.

### K04 — automatic boot-chain activation and fallback (M)

Dependencies: K03, K02, D01 hardware proof. Files: boot-chain selection/confirmation handlers, watchdog integration, host status and failure tests.

- Activate the candidate with bounded reset/confirmation behavior; recover automatically from bad or hanging candidate code and interrupted activation/confirmation.
- Never label atomic writes alone as bad-code recovery or require a manual power cycle. Keep the fallback usable through subsequent updates.
- Verification: planned boot-chain runner `--case activation`, then `tools/hardware/boot_chain_acceptance.py` with actual bad loader binaries and physical power cuts. Production enablement waits for that mechanism's evidence.

### K05 — independent recovery target (M; split image/repair API PRs)

Dependencies: D01, D02, K01, K02; signed repair backend from U01/U02 before repair is enabled. Files: minimal recovery image configuration/payload, boot selection, recovery service, artifact tests.

- Boot independently of A/B, DATA and Podman when slots are exhausted; replace infinite counter reset with stable recovery entry.
- Offer authenticated local upload/USB repair using the same trust/compatibility checks; preserve DATA and the recovery route during repair. Bound temporary memory/storage and include a no-Internet path.
- Verification: planned `python3 tools/test_recovery_image.py`, absent-rootfs/DATA artifact checks; real both-slots-failed and interrupted repair cases in H01.

### K06 — DATA-independent recovery dashboard (M)

Dependencies: D02, K02, shared recovery components from K05. Files: DATA mount/service ordering, recovery UI/API, normal health routing, tests.

- Start useful recovery without DATA, application container or its session secrets. Stop normal DB writes and avoid repeatedly exhausting healthy OS slots for the same shared-storage fault.
- Keep safe filesystem checks; require authorization for additional repair/restore and never silently format DATA. Reuse the independent target's UI and authorization where possible.
- Verification: planned `python3 tools/test_data_recovery.py`, normal/failed/read-only/full DATA and recovery-auth cases, then hardware coverage in H01.

### U01 — RAUC trust and signed bundle production (M)

Dependencies: D02, K01. Files: RAUC config, bundle manifest/build/signing tooling, protected release workflow and tests.

- Embed separate dev/release public trust anchors as approved; keep CA private keys offline and release signer access restricted. Key provisioning is a separate controlled operation, never a committed secret.
- Build authenticated board-specific bundles containing the complete per-slot Linux unit; implement/test clock validity, rotation, compatibility and package integrity. Do not weaken verification in recovery.
- Verification: `python3 tools/check_rauc_conf.py`, planned `python3 tools/test_os_bundle.py`; signed install plus unsigned/wrong-CA/tamper/wrong-board/clock/rotation negative cases. Configure credentials only when executing the approved key setup, without printing secrets.

### U02 — host OS installation state machine (M)

Dependencies: U01, S01, K02, K04/K05 compatibility. Files: `epilykos_update.py` or dedicated OS module, host allowlisted API/service, USB scanner and tests.

- Support official online retrieval, local bundle staging and USB import with one verified inactive-slot installation path and durable progress/result state.
- Serialize maintenance, preserve active slot/DATA and handle browser disconnect or interrupted transfer/install without duplicate activation. Default installation remains manual; no USB autorun.
- Verification: planned `python3 tools/test_os_update.py`, mocked RAUC failures plus actual signed-bundle install/rollback cases under H01.

### U03 — dashboard OS update controls (M)

Dependencies: U02. Files in app: `routes/updates.js`, host capability/API bridge, update settings UI and tests.

- Show source/release, preflight outcome and expected outage before authenticated Install and reboot; support bounded local upload and USB selection.
- Report installation, reboot/reconnect, confirmation or rollback, and hide unsupported actions on older hosts. Preserve manual OS policy and existing opt-in app auto-update behavior.
- Verification: `npm test` including absent/older host, rejected bundle, concurrent job and browser disconnect/reconnect; offline UI install in H01.

### A01 — self-contained signed app package builder (M)

Dependencies: D02 package schema. Files: release/package/signing tools, app artifact workflow and package tests across repos.

- Include complete platform OCI content plus signed inventory binding authorized manifest, archive sizes/hashes and platform identities. Correctly relate a signed multiarch index digest to the included image graph.
- Reuse the app signing authority, keep channel/sequence authorization and source provenance intact, and publish packages alongside appropriate releases. Do not execute package hooks.
- Verification: planned `python3 tools/test_offline_app_package.py`, image substitution/platform mismatch/missing blob/path traversal/archive expansion fixtures.

### A02 — offline app import and install (M; split host/UI PRs)

Dependencies: A01, S01, S04 migration recovery. Files: app updater host module, authenticated import endpoint/USB path, update UI and tests.

- Import verified content without network access, enforce existing channel/sequence floor and use the same health/promotion/rollback path as online installs.
- Require explicit local install authorization, bound import resources and retain a usable prior image/database combination; no normal host reboot or OS slot switch.
- Verification: extend `python3 tools/test_app_update.py`, `npm test`, real network-blocked upload/USB tests in H01. Include corrupt/missing anti-replay state and database-restore interactions.

### O01 — redacted local diagnostics (M)

Dependencies: K06 authorization/interface; normal-mode collectors can precede K06. Files: host diagnostic collector, normal/recovery endpoints, app download UI and tests.

- Bound time/output, redact both structured results and logs, omit raw DB/secrets, and return useful partial reports when components or DATA are missing.
- Generate an authenticated local download without automatic transmission, unrestricted command arguments or unbounded staging.
- Verification: planned `python3 tools/test_diagnostics.py`, `npm test`, seeded-secret, stalled-collector, full-disk and no-DATA cases.

### O02 — read-only application runtime (M)

Dependencies: S03–S05, A02/U03 writable paths. Files: `epilykos-run`, app entrypoint, identified runtime write paths and tests.

- Audit first boot, normal settings/session/DB, BLE/serial, remote-access helpers, cache/temp files and all maintenance. Move writes to approved DATA or bounded scratch; inspect implicit writable mounts.
- Enforce read-only root without broad writable source mounts. Preserve BOOT-file password import and host maintenance, and avoid silent read-write fallback.
- Verification: read-only container workflow matrix plus `npm test` and host tests; actual production-size restore, pairing and password-reset cases under H01.

### R01 — finish automatic stable publication (M)

Dependencies: B03, U01/U03, A01; initial supported mechanism acceptance H01 before v1 launch. Files: OS/app release workflows, artifact/provenance validation, release-gate tests.

- Bring the full OS build workflow onto main through the existing maintainer process; publish the accepted build output with signatures, hashes, provenance and Imager metadata. Stable inputs must come from main/stable app records.
- The maintainer merge remains authorization. Failed reliable checks, missing artifacts and wrong-channel/provenance must block publication across dispatch/push/release/app-bridge/retry paths; avoid duplicate release races.
- Verification: planned `python3 tools/test_release_gates.py` and negative dispatch/artifact cases; one end-to-end stable pipeline exercise when release is authorized. No added per-image manual approval or unconditional current-QEMU gate.

### H01 — initial platform acceptance harness (M; evidence run separate)

Dependencies: relevant feature before its test, baseline B01; does not prevent writing harnesses early. Files: scripted cases under `tools/hardware/`, disposable fixture generation, structured evidence schema/runbook.

- Script the approved initial A/B, bootloader, both-slots, DATA, offline update, restore, pairing and watchdog cases with explicit phase markers and automatic result collection.
- Provide a 72-hour production-size soak runner and power-control adapter boundary. Physical power cuts need an attached controllable supply/relay and Pi; safely create disposable test media/data instead of assuming permission to damage a live installation.
- Verification: planned `python3 tools/hardware/run_acceptance.py --device-config DEVICE.json --suite pi3-v1` and `--suite soak-72h`; record actual hardware evidence separately from harness unit tests. Repeat targeted cases when their mechanism changes, not manual testing of every routine build.

### T01 / T02 — later Pi 4 / Pi 5 best-effort targets (separate M slices)

Dependencies: Pi 3B core path complete; do not block Pi 3B release. Files per board: defconfig, pinned upstream boot/firmware selection, board image layout, compatible identity, independent CI matrix row and support notes.

- Add Pi 4 first, then Pi 5 using upstream-supported board mechanisms and shared logical A/B/DATA semantics; do not assume Pi 3B physical layout/recovery works unchanged.
- Publish only a board's successfully validated artifact and label evidence/limitations honestly. A failed Tier 2 job must not suppress a valid Pi 3B release.
- Verification: full board build, planned artifact/board-compatibility checks and cross-board update rejection; distinguish build-only support from real board evidence.

## Checkpoints and progress

| Checkpoint | Work | Status | Exit evidence |
|---|---|---|---|
| Baseline | B01–B03 | Pending | Viable image, aligned contracts, truthful required/diagnostic checks |
| Foundation | D01–D02 | Pending; physical evidence unavailable here | Protected boot/recovery design and auth/trust ADRs with measured feasibility |
| Data safety | S01–S05 | Pending | Production-size transfer/restore/snapshots with bounded resources and interruption recovery |
| Boot recovery | K01–K06 | Pending | Complete per-slot Linux, bounded fallback, bad-loader recovery and independent recovery |
| Update delivery | U01–U03, A01–A02 | Pending | Signed online/local/USB installs with offline recovery and replay/channel enforcement |
| Runtime support | O01–O02 | Pending | Redacted degraded-state export; all required writes work with read-only root |
| Pi 3B launch | R01, H01 | Pending | Initial acceptance/soak evidence, automated publication with demonstrated negative gates |
| Later boards | T01, T02 | Deferred until core complete | Independent best-effort board artifacts/support labels |

At each checkpoint update this table, tests and implementation/evidence statuses. Land compatible app changes before the OS image that preloads them, and expose new host-dependent controls only after capability negotiation. Keep fixes green and reviewable; use paired PR links when a vertical feature spans repositories.

## Always / never

Always preserve DATA and known-good recovery/rollback assets, validate signatures before authorization-sensitive activation, enforce resource bounds and report unavailable evidence honestly. Keep exact artifact/source/board identity with hardware results. Ordinary progress does not depend on an emulator pretending to implement missing Pi firmware.

Never auto-format DATA, silently repartition existing cards, reset replay protection to make an old package install, ship private signing keys, publish known-failing board artifacts, hide a failed required check, reintroduce routine manual image approval or claim unproven automatic recovery. Do not merge or promote stable without the user's existing maintainer authorization.

Out of scope for v1: custom update URLs, external application downgrade override, USB gadget provisioning, hardware-anchored secure/verified boot and cryptographic rootfs integrity enforcement. Pi 4/5 do not delay Pi 3B.

## What can proceed without hardware

B01–B03, S01–S05, package/trust test tooling, diagnostics collectors and scripted regression/hardware harnesses can progress without an attached Pi. D01 source investigation and design can progress too, but physical fallback/power-cut proof cannot be fabricated. Do not hold all useful work behind that evidence; keep the production boot-chain/update dependency gated until it exists. If Pi 3B cannot meet the locked requirement with the protected boundary selected, report the precise limitation and measured alternatives before changing scope or hardware.

No additional scope choices are requested by this plan. Remaining low-level design details are implementation work; surface a user decision only if evidence requires changing a locked policy, storage migration, hardware commitment or secret-custody arrangement.
