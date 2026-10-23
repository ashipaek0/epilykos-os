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

## Decisions of 2026-10-22 (OS updates from Settings)

Decided by the maintainer; the design is [os-dashboard-updates-design.md](os-dashboard-updates-design.md).

| ID | Decision |
|---|---|
| D1 | OS updates from Settings replace a whole ROOT slot (kernel, DTB, modules, rootfs, bundled app; R02A) and ship once they pass on hardware. The boot chain on BOOT is fixed at flashing and changes only by reflashing. R02B, R02C and R17C stay v1 work as a separate track (Track B) that does not gate slot updates. |
| D2 | The maintainer generates and keeps the OS release CA, the OS development CA and the OS index authorization root offline. That offline index root signs a policy delegating channel-scoped CI index keys; CI holds only delegated index and RAUC leaf signing keys in protected environments. |
| D3 | A spare SD card is available for destructive tests. There is no power-control rig or serial cable; power-cut tests are done by hand. |

## Consistency resolution of 2026-10-23

The maintainer requested the recommendations resolving the design review.
D1–D3 remain the scope/custody/hardware decisions. Their implementation rules
are recorded in the linked design and plan:

- **Track A:** per-slot Linux updates, native signing plus delegated index
  authorization, shared trust/floors in the existing redundant U-Boot env,
  safe maintenance transactions, dashboard installation and their own real
  Pi acceptance. Online delivery is tasks 1–8 plus task 11's online gate;
  task 9 and its acceptance add offline delivery. No new raw recovery layout
  or independent recovery enrollment is a Track A prerequisite.
- **Track B:** task 10 owns the early boot-chain updater, automatic pre-U-Boot
  fallback and independent both-slot/DATA-failure recovery (R02B, R02C, R17C
  and the independent UI part of R04). It does not gate Track A. Until then,
  exhausted slots stop safely with diagnostics instead of a counter-refill
  loop; this endpoint is not advertised as independent recovery.
- Refresh RAUC attempt counters on every locally healthy boot. Use the single
  1200 s health deadline and demonstrated loader/kernel/systemd watchdog
  handoff; persisted confirmation does not exempt normal boot health.
- Persist activation intent before mark-active and confirmation intent before
  mark-good. Retain a durable pending guard from ready through the terminal
  result, including RAUC writes that outlive their initiating worker.
- Native RAUC verification and a delegated signed per-package authorization
  are mandatory online and offline. Shared offline-root-signed trust policy
  advances monotonically outside DATA and is enforced by both slots, so a
  rollback/factory reset cannot revive a locally revoked signer. Unknown
  trust/floor state refuses installation. No sequence counter is claimed to
  revoke a signing key by itself.
- Existing env redundancy is supplemented by critical-state generation/
  selection before loader writes, verified replication before commit
  acknowledgement and fail-closed ambiguity handling. Losing one copy after
  acknowledgement must not revive an older policy/floor; this is tested
  separately from corrupting both copies. No tamper-proof hardware counter
  or new raw storage region is implied.
- Existing successful app updates stay independent. A new bundled app remains
  tentative until OS confirmation; fallback app/database compatibility is
  tested, not inferred solely from an image label. Bootstrap/adoption and a
  deliberate factory reset have their explicitly documented state semantics.

These are design requirements, not claims that the mechanism is implemented,
that production keys have been provisioned, or that physical tests passed.

## Design and evidence still outstanding

- Track B: demonstrate the Pi 3B pre-U-Boot fallback/reset mechanism and define the protected earliest bootstrap boundary. Atomic writes alone do not establish recovery from a valid but hanging loader.
- Track B: select the independent recovery payload location and define safe installation for existing cards. Do not silently repartition deployed cards.
- Track B: define recovery authorization without DATA-backed passwords/session secrets, including credential provisioning and reset.
- Specify certificate validity, clock handling, rotation/revocation and recovery trust-anchor updates.
- Define exact signed offline package serialization, per-operation peak storage budgets and interruption-safe database/update transactions.
- Verify app/database migration compatibility under image rollback. OS bundle replay/version policy is separate from the locked application-sequence override decision.
- Choose repeatable physical test tooling and collect actual evidence. No access to a physical test rig is assumed.

## Implementation dependencies

1. Preserve the viable current baseline and the main-to-dev synchronization already completed; the original build failure below is historical.
2. Track A: finalize protocol-2 bootstrap, existing-env state/trust budgets and slot watchdog behavior; then implement the slot/package/host/app path with its own acceptance and release gates.
3. Track B: separately resolve early selector and independent recovery feasibility/storage before implementing that updater. Its unresolved hardware design does not block Track A.
4. Update machine-readable contracts and generated documentation with the owning implementation PRs, preserving truthful pending evidence and Track A/B scope.
5. Complete each track's initial hardware acceptance and automatic publication gates under R15; do not claim full v1 recovery while Track B is pending.

## Decision-review baseline and original failure

Decision-review source baseline: epilykos-os dev `4baf9fd42f83469c100e3d4dccd9a925e0c3eaff`; main `2b212af1d358ac8d64a7568cb8dfb93e8eef5a87` when inspected on 2026-10-17. The full OS build workflow is on dev and must reach main to complete the stable OS automation.

Build run https://github.com/ashipaek0/epilykos-os/actions/runs/39031562530 failed during Buildroot rootfs creation/cleanup with permission-denied removal of staged Bluetooth configuration files. Its QEMU job did not reach boot testing because there was no image artifact. This is an actual build failure, distinct from the earlier QEMU limitations reported by the user.

The decision review and this planning commit changed planning documents only. It did not implement source fixes, change GitHub secrets/protections, publish releases or perform hardware tests.

Execution update: B01 repair PR #45 (`20e3eee`) passed full Pi image build/structure in run 39036230265 and provides the test image artifact. This repairs the original build failure; the remaining feature and physical evidence work is still pending.
