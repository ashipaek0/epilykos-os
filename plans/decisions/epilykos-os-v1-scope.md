# EpilykosOS v1 — approved scope and policies

Approved in the decision review on 2026-10-17. These are requirements and scope decisions, not a claim that implementation or acceptance testing is complete. The detailed approval history remains in the originating Codex workspace at outputs/epilykos-os-decision-log.md; this file is the consolidated approved policy. Superseded proposals in that history are not requirements.

## Locked decisions

| ID | Approved policy |
|---|---|
| R01 | Keep MBR for Pi 3B; no card migration authorized by this decision. |
| R02A | Kernel, Linux DTB, modules and rootfs are one update/rollback unit per A/B slot. |
| R02B | Include a dedicated early boot-chain updater in v1, with power-cut and bad-bootloader recovery tests. |
| R02C | Boot-chain recovery is fully automatic, with no manual power cycle, SD removal or second computer. Pi 3B feasibility must be demonstrated. |
| R03 | Allow 20 minutes from Linux userspace startup per attempt, three candidate attempts, then known-good fallback. Automatic reboot/watchdog coverage is required; Internet and inverter availability do not gate health. |
| R04 | Provide DATA-independent recovery UI; keep safe checks, stop normal database writes when DATA is unusable, require authorization for repair/restore and avoid endless reboot loops. Never automatically format or discard the database. |
| R05 | Use native RAUC signing with separate development/release CAs and separate app keys, offline CA custody and protected CI signing. Test rejection, rotation and offline clock handling. |
| R06 | Support official online OS delivery, authenticated upload and USB import. OS installation is manual in v1 through an authenticated Install and reboot action; serialize conflicting maintenance. |
| R07 | Support production-size backup/restore with bounded streaming to DATA, consistency/integrity checks, recoverable replacement and retention of the original until restored app health succeeds. |
| R08 | Create daily streaming-gzip snapshots; retain the two newest successful copies, verify replacement before pruning, support legacy backups and warn on missed/failed snapshots. |
| R09 | Warn below max(20% DATA capacity, 2 GiB). Admit maintenance only if projected peak use leaves max(10%, 1 GiB) free. Account for scratch/rollback/expansion and protect retained data/images. Keep 32 GB minimum, 64 GB recommended, without promising all operations fit on 32 GB. |
| R10 | Publish complete signed offline application packages including required OCI content. Support authenticated upload/USB, content/platform/channel/sequence checks and offline health/rollback tests. |
| R11 | Defer custom update-server URLs from v1. |
| R12 | Defer external application downgrade overrides from v1; preserve replay protection and internal known-good rollback. |
| R13 | Provide authenticated, bounded, redacted diagnostics downloads in normal and recovery modes, with partial results when components fail. No database contents or automatic transmission. |
| R14 | Require read-only application root with explicit DATA/scratch writes, preserving host-controlled password reset and maintenance. Verify all affected workflows first; no silent read-write fallback. |
| R15 | Keep the existing maintainer merge as release authorization, then automate checks and publication. Reliable failures block release; current QEMU stays diagnostic until validated. No routine manual image approval. |
| R16 | Complete Pi 3B first; add Pi 4 then Pi 5 as non-blocking best-effort images with separate automated validation and accurate support labels. |
| R17A | Defer USB gadget provisioning; keep USB-stick update import, USB serial and existing network/BLE/BOOT-file setup. |
| R17B | Defer verified/secure boot and cryptographic rootfs integrity enforcement. Keep signed updates/read-only operation and document their limits. |
| R17C | Include independent recovery for both failed Linux slots: automatic entry, authenticated offline OS repair, DATA preservation and no rootfs/DATA/container dependency. Storage placement is not yet designed; no fifth partition assumed. |
| R18 | Establish initial real Pi 3B platform/recovery evidence and a 72-hour production-size soak, then automated regressions with targeted hardware revalidation. Align source contracts and generated documentation; do not introduce per-image manual approval. |

## Design and evidence still outstanding

- Demonstrate the Pi 3B pre-U-Boot fallback/reset mechanism and define the protected earliest bootstrap boundary. Atomic writes alone do not establish recovery from a valid but hanging loader.
- Select the independent recovery payload location, fit it into the agreed storage/boot design and define safe installation for existing cards. Do not silently repartition deployed cards.
- Define recovery authorization without DATA-backed passwords/session secrets, including credential provisioning and reset.
- Specify certificate validity, clock handling, rotation/revocation and recovery trust-anchor updates.
- Define exact signed offline package serialization, per-operation peak storage budgets and interruption-safe database/update transactions.
- Verify app/database migration compatibility under image rollback. OS bundle replay/version policy is separate from the locked application-sequence override decision.
- Choose repeatable physical test tooling and collect actual evidence. No access to a physical test rig is assumed.

## Implementation dependencies

1. Repair the current image-build failure and produce a viable baseline image.
2. Resolve boot-chain/recovery feasibility and storage before committing to the final update layout.
3. Update machine-readable contracts and their generated narrative to reflect the approved policies, keeping implementation/evidence statuses honest.
4. Implement the host/application changes with reliable automated failure tests and release publication gates.
5. Complete the initial hardware acceptance suite and enable normal automatic stable publication under R15.

## Decision-review baseline and original failure

Decision-review source baseline: epilykos-os dev `4baf9fd42f83469c100e3d4dccd9a925e0c3eaff`; main `2b212af1d358ac8d64a7568cb8dfb93e8eef5a87` when inspected on 2026-10-17. The full OS build workflow is on dev and must reach main to complete the stable OS automation.

Build run https://github.com/ashipaek0/epilykos-os/actions/runs/39031562530 failed during Buildroot rootfs creation/cleanup with permission-denied removal of staged Bluetooth configuration files. Its QEMU job did not reach boot testing because there was no image artifact. This is an actual build failure, distinct from the earlier QEMU limitations reported by the user.

The decision review and this planning commit changed planning documents only. It did not implement source fixes, change GitHub secrets/protections, publish releases or perform hardware tests.

Execution update: B01 repair PR #45 (`20e3eee`) passed full Pi image build/structure in run 39036230265 and provides the test image artifact. This repairs the original build failure; the remaining feature and physical evidence work is still pending.
