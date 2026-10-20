# EPILYKOS-OS-CONTRACTS

**Version:** {{VERSION}}
**Architecture baseline:** EpilykosOS Appliance Architecture v0.5, superseded where the 2026-10-17 v1 decision review specifies otherwise.
**Hardware:** Pi 3 Model B reference target; Pi 4 then Pi 5 later best-effort targets.
**Scope:** Track 2 appliance only. Ordinary Docker Compose deployment remains a separate product.

> The YAML registry is normative. Approval defines a requirement; it does not prove implementation or hardware acceptance. Sections 2, 3, 4 and 7 are generated from `epilykos-os-contracts.yaml`; `make contracts` rejects stale rendering. The approved scope and implementation sequence are recorded under `plans/decisions/` and `plans/pending/`.

## 0. Repositories, branches and releases

| Repository | Responsibility |
|---|---|
| `ashipaek0/Epilykos` | Application source and OCI images; Bluetooth operates inside the application container. |
| `ashipaek0/epilykos-os` | OS images, host services, boot/recovery, RAUC, image manifests, signing and appliance release automation. |

Both use `dev` for integration and `main` for stable releases. The maintainer's existing merge decision authorizes a release; there is no additional routine manual image approval. Implementation PRs target `dev`; agents do not infer permission to merge or publish stable from permission to prepare a PR.

Application builds publish immutable digest records with source commit/ref. The release bridge authenticates the successful source build, updates a channel-specific manifest-only PR against the corresponding `dev` or `main` branch, validates it and merges that generated update. It then dispatches signing and, for stable, the OS build. The application source merge remains the maintainer's release decision. The full OS workflow must be present on `main` before its stable dispatch can work.

Stable artifacts use `main` source and stable application provenance. Reliable automated build, artifact, configuration, signature and provenance failures block publication. QEMU is diagnostic until calibrated against known-good and deliberately broken images; it neither substitutes for Pi firmware nor proves physical boot recovery. Initial Pi 3B acceptance and targeted revalidation of changed boot/storage/recovery mechanisms remain required, without manually testing each routine image.

OS publication is separate from appliance installation. OS installation is manual through authenticated Install and reboot; automatic checks and USB insertion do not authorize installation. Application automatic installation remains opt-in, using signed release authorization. Offline packages can be installed through authenticated upload or USB without Internet.

### Hardware support

Pi 3B (1 GB, microSD) is implemented first. Pi 4 then Pi 5 use separately pinned upstream board support, images, compatible identities and independent validation. They do not block Pi 3B v1. A failed board artifact is withheld; an untested image is labeled hardware-unverified. Logical A/B plus persistent DATA semantics are shared, but Pi 3B physical layout, MBR choice and boot-chain implementation are not assumed portable.

## 1. Terms and evidence states

MUST is required, SHOULD needs a documented exception, and MAY is optional. Contract states mean:

- `draft`: approved requirements, incomplete implementation or evidence.
- `decision-required`: a concrete decision remains open.
- `blocked-on-spike`: an implementation claim depends on feasibility evidence.
- `deferred-v1`: explicitly outside v1.
- `passed`: all required evidence exists and has been reviewed.

Contract validation is not a boot test and does not automatically turn a green build into a passed hardware contract. Missing physical evidence must remain pending. Normal application health, degraded DATA recovery and independent both-slots-failed recovery are distinct states.

## 2. Frozen invariants

{{INVARIANTS}}

## 3. Contracts

{{CONTRACTS}}

## 4. Acceptance matrix

{{ACCEPTANCE}}

### Fixture reuse

Reuse common offline-boot fixtures for network independence, persistent OCI storage and read-only operation, recording each assertion separately. Use a disposable production-size database copy for large maintenance tests. Mock termination is not physical power-cut evidence. Negative tests must demonstrate that deliberately broken inputs fail the relevant check.

## 5. Initial acceptance and later regression

| Stage | Required initial mechanism evidence |
|---:|---|
| 0 | Pi 3B MBR/layout and protected boot/recovery feasibility; both Linux slots and forced fallback; no hidden card migration. |
| 1 | Ethernet, BOOT/Imager-file and Improv BLE Wi-Fi provisioning; bounded re-entry, site timezone, offline clock restoration. USB gadget setup is deferred. |
| 2 | Approved rootful Podman with application uid/gid 1000; preloaded offline image startup, verified writable paths and read-only container; production-size 72-hour memory/storage/polling soak and correct release provenance. |
| 3 | Real BLE BMS/inverter and serial connections, pairing/bond lifecycle and Wi-Fi coexistence; no privileged container or unexplained capability expansion. |
| 4 | Per-slot Linux artifacts, one 20-minute userspace deadline and automatic watchdog/fallback, DATA-independent recovery, SQLite durability, large recoverable backup/restore, compressed snapshots, storage admission, diagnostics and access/password-reset behavior. |
| 5 | Signed OS/boot-chain and complete offline app installation; rejected tampering/wrong-board/replay; independent rollback with compatible DB state; bad-loader and both-slots recovery; repeated physical cuts through write/activation/confirmation/repair; automatic publication failure gates. |
| 6 | Explicitly deferred custom URLs, external app downgrade override, USB gadget setup and verified boot require later approval before implementation. |

Initial platform acceptance is followed by automated regressions. Refresh targeted physical evidence when the relevant low-level mechanism changes, using repeatable harnesses and automated physical testing where available. Do not add a per-image manual approval gate. Pi 4/5 best-effort support is non-blocking and separately labeled.

## 6. Decision governance

Decisions are ordered by due stage then ID; chronology does not change their force. A stage cannot close with unresolved decisions due at or before it. The 2026-10-17 maintainer review locked 22 scope/policy choices; `D-VREVIEW-001` links the consolidated record. Remaining implementation details include the protected earliest bootstrap boundary, recovery storage/authorization, clock/trust rotation and safe adoption for existing cards. If measured hardware behavior invalidates a locked requirement, report the evidence before changing it.

## 7. Recorded decisions

{{DECISIONS}}

## 8. CI responsibilities

`make contracts` validates registry syntax/IDs/states/decision references, configured releasable-stage decisions, generated-document freshness and the launcher/manifest rules supported by `tools/validate_contracts.py`. `make test` exercises host/validator regressions. Image build/structure, signed package rejection, update state, archive parsing and runtime tests are additional checks in the owning feature workflows; do not claim the static validator already proves those behaviors.

Reliable automated failures must prevent the affected artifact from being published. Checks must cover, as their features land:

- Immutable digests, stable source provenance and authenticated manifest/package content.
- Rejection of external application sequences at/below the promoted floor, without blocking internal known-good rollback or silently resetting that floor.
- Default-off SSH with non-root password/public-key access when explicitly configured; root login rejected.
- Board-specific artifact/compatible identity, logical A/B/DATA, slot-contained Linux and preserved known-good recovery assets.
- Bounded maintenance allocations, upload/expansion limits, valid restore transactions and no automatic data deletion.
- Read-only runtime with explicit writable paths; signed online/local/USB installation and redacted bounded diagnostics.
- Correct handling of missing/failed artifacts and all publication triggers, including retries and the application bridge.

A missing artifact should suppress dependent boot execution and report the real prerequisite failure. Hardware evidence records the board, media, source/artifact identity and measured outcome; an unavailable capability is PENDING or an approved skip, never a fabricated PASS. QEMU limitations remain diagnostic until validated.

## 9. Application integration

| Area | Reviewed baseline / remaining work |
|---|---|
| Health, stdout logging, SQLite mode, timezone | Implemented baseline; preserve local health independence and verify under new maintenance/runtime behavior. |
| Host clock/card notices, SSH controls, password reset, pairing | Implemented baseline; preserve approved password/key login, BOOT reset and bounded pairing. Independent recovery authorization is still design work. |
| Online application update | Signed/provenance/replay and opt-in update path exists; full offline OCI packages, archive/content validation and migration-compatible rollback need implementation/evidence. |
| Backup/restore | Existing small RAM-staged uploads/direct replacement must be replaced by bounded DATA streaming and recoverable multi-GB transactions. |
| Snapshots/storage | Existing two plain DB copies need gzip, durable publication, admission limits and overdue/failure warnings. |
| OS install and diagnostics | Host capability/API and authenticated normal/recovery UI remain pending. |
| Read-only container | Mandatory v1 requirement; audit runtime helpers and writable paths, then enforce and test without silent fallback. |

App changes land through focused PRs into application `dev`, with backward-compatible host capability negotiation. The OS preloads a verified released app image, never uncommitted application source. Do not expose unsupported host operations on older images.

## 10. Evidence and execution records

Store evidence under `evidence/stage-N/` or a stable uploaded artifact. Name the board/revision/media, firmware/source and tested artifact, test phases, outcomes and measurements. Record initial 72-hour soak, physical cut/bad-loader/restore transitions and relevant source behavior without claiming a simulated test is physical evidence.

The plan progress table tracks implementation PRs separately from hardware results. Repeated normal releases use automated checks and publication after the existing maintainer authorization. Initial acceptance and changed-mechanism tests do not become routine manual image approval.

## 11. Architecture versus completion

The approved scope is a contract for implementation, not a claim the platform is complete. Boot-chain and independent recovery feasibility are outstanding until proven, signing keys are not generated by documentation, and queued features stay pending. Rootfs read-only policy is not secure boot or cryptographic integrity. Direct SD modification/physical media failure is outside the deferred verified-boot guarantee.

## 12. Changelog

{{CHANGELOG}}
