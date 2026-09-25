# EPILYKOS-OS-CONTRACTS

**Version:** {{VERSION}}  
**Architecture baseline:** *EpilykosOS: Appliance Architecture — v0.5*  
**Reference platform:** Raspberry Pi 5 (8 GB)  
**Scope:** Track 2 (EpilykosOS appliance) only. Track 1 (ordinary Docker Compose deployment) remains outside this contract.

> This document converts the v0.5 narrative architecture into implementation contracts and acceptance gates. A contract marked **decision-required** or **blocked-on-spike** is deliberately not silently completed: CI must treat it as unresolved until the named decision/evidence exists.
>
> Sections 2, 3, 4 and 7 are generated from `epilykos-os-contracts.yaml` (the machine-readable source of truth) by `tools/render_contracts.py`; `make contracts` fails if this document is stale.

## 0. Repositories, branches and releases

Track 1 and Track 2 are separate products (`I-001`) and live in separate repositories:

| Repository | Holds | Consumes |
|---|---|---|
| `ashipaek0/epilykos` | The Epilykos application and its OCI images (`epilykos`, `epilykos-bms`). Appliance-facing application changes land here as small pull requests into `dev`. | — |
| `epilykos-os` | This contract, the OS image build, RAUC configuration, Quadlets, provisioning, and CI (`make contracts`). | Application images **by digest only**, via application manifests. |

Both repositories use the same branch model:

- **`dev`** — integration branch. Builds from `dev` may be used by developer images and the explicitly labelled `dev` channel only.
- **`main`** — stable releases only, updated by merging `dev` after review. Stable appliance releases reference only application images built from `main` (`I-009`, `C-RELEASE-001`).

Every application image build publishes a digest record (`service, image, digest, tag, commit, ref`). Stable manifests are assembled from `main` records; tags such as `latest` and `dev` are only ever used to discover a build, never to pin one.

## 1. Normative language and contract states

**MUST** is release-blocking for the referenced stage. **SHOULD** requires an explicit documented exception if not followed. **MAY** is optional.

Contract states:

- `draft` — requirement is sufficiently defined to implement and test, but has not yet produced passing evidence.
- `decision-required` — a concrete value/scope decision remains open.
- `blocked-on-spike` — implementation depends on hardware evidence.
- `deferred-v1` — intentionally out of the v1 critical path.
- `passed` — all required evidence exists and has been reviewed.

CI validates that every contract required by a stage is either `passed` or explicitly excluded by that stage's policy; unresolved contracts must never be interpreted as passing.

## 2. Frozen invariants

{{INVARIANTS}}

## 3. Contracts

{{CONTRACTS}}

## 4. Acceptance matrix

{{ACCEPTANCE}}

### Test-fixture reuse

`C-NET-001`, `C-DATA-002`, `T-RUNTIME-001` and `T-RUNTIME-003` deliberately overlap around an offline cold boot. They verify different properties — absence of network dependency, persistence of OCI storage across slots, successful runtime startup, and a read-only container root — but CI SHOULD use one shared network-isolated boot fixture and record separate assertions/evidence instead of creating unrelated test rigs.

## 5. Stage gates

A stage is complete only when every test assigned to that stage passes and every contract needed by that stage is no longer `decision-required` or `blocked-on-spike`, except where the roadmap explicitly marks the feature deferred.

| Stage | Gate |
|---:|---|
| 0 | The A/B boot chain and build system are proven on the reference Pi 5 and v1 medium (`C-BOOT-000`); partition topology, update-domain split, reference hardware and v1 recovery scope are frozen where due; the complete decision backlog is reviewed and every open decision has an explicit due stage. |
| 1 | Ethernet, Wi-Fi AP and USB provisioning paths work from a clean flash without terminal access; provisioning captures the site time zone. |
| 2 | Rootless Podman host contract is proven; Epilykos starts from preloaded `main`-built images with no network present and with a read-only container root; release channels and digest provenance are enforced. |
| 3 | Serial/RS232/RS485 and BLE hardware tests pass; BMS bridge privilege set is evidence-based and `privileged=true` is absent unless exception-approved. |
| 4 | Appliance policy is active: read-only root, persistent DATA, logging policy, SSH default-off/key lifecycle, U-Boot/RAUC bootchooser (or the mechanism chosen by `D-BOOT-002`), corrupted-environment recovery, power-cut telemetry-loss bound, and boot-health separation all pass hardware tests. |
| 5 | Signed RAUC updates and trusted, replay-protected, digest-pinned, stable-channel OCI application updates both pass independent authenticity, success/failure, and rollback tests. |
| 6 | Optional verified boot and user-specified update URL are implemented only if separately approved. |

## 6. Decision governance

The decision registry is **not** ordered by when a decision was introduced. It is ordered by `due_stage`, then decision ID. A decision added in a later contracts revision has exactly the same review and release-gating semantics as one present from the first pass.

Stage 0 must review the **entire** open decision backlog and confirm each decision's due stage. Only decisions due at Stage 0 have to be resolved before Stage 0 closes; later-stage decisions remain open but visible. A stage cannot close while an open decision whose `due_stage` is that stage or earlier remains unresolved.

This explicitly applies to decisions introduced after v0.1: `D-ACCESS-001` and `D-UPDATE-001` (v0.2), and `D-PLATFORM-001`, `D-BOOT-002` and `D-RELEASE-001` (v0.4).

## 7. Decisions that must not be guessed

{{DECISIONS}}

## 8. CI contract

The repository provides a single contract-validation entry point:

```text
make contracts
```

That target MUST fail when:

- the machine-readable registry is syntactically invalid;
- a contract ID, acceptance-test ID, invariant ID or decision ID is duplicated;
- any decision lacks `due_stage`, `status`, or Stage-0 review visibility, or blocks a contract that does not exist;
- a contract's `unresolved` list names a decision ID that does not exist;
- a stage marked releasable still contains an open decision whose `due_stage` is that stage or earlier;
- the rendered `EPILYKOS-OS-CONTRACTS.md` is out of date with the registry;
- production Quadlets contain `privileged=true` without an approved exception marker;
- the Epilykos Quadlet does not set `LOG_TO_FILE=false` and an explicit `SQLITE_SYNCHRONOUS`, or its `HealthCmd` does not call `/healthz`;
- production application manifests use mutable OCI tags instead of `sha256:` digests, omit `channel`, or have a non-integer / non-positive `sequence`;
- a `channel: stable` manifest references a digest without a `main` digest record;
- an application candidate can be activated without satisfying `C-UPDATE-004` manifest trust;
- an externally supplied application manifest with sequence at or below the stored promoted-release floor can be activated without an explicit authenticated downgrade exception;
- appliance SSH is enabled by default or permits password/root login;
- developer and appliance partition topologies diverge;
- required appliance OCI images are not present in the image manifest/preload set.

Checks whose inputs do not exist yet (Quadlets, manifests, image builds) run as soon as the corresponding directory appears. Hardware-dependent tests MUST emit machine-readable evidence rather than being silently skipped. A permitted skip must name the missing hardware capability and must not count as a pass for a release gate.

## 9. Application changes required by the contract

These land in `ashipaek0/epilykos` as small pull requests into `dev`, and reach appliances only through a stable (`main`) release.

| # | Change | Contract | Status |
|---:|---|---|---|
| 1 | Dedicated unauthenticated `GET /healthz` (no secrets; HTTP + SQLite schema only; 503 when unhealthy) and a Docker `HEALTHCHECK` | `C-RUNTIME-002`, `C-BOOT-003` | Done (app commit `fabfa89`) |
| 2 | `LOG_TO_FILE=false` → stdout/stderr only, no log directory created (required for a read-only root) | `C-RUNTIME-002`, `C-LOG-001` | Done (`fabfa89`) |
| 3 | Explicit `SQLITE_SYNCHRONOUS` (OFF refused) and named `METRIC_FLUSH_INTERVAL_MS` loss window, both reported by `/healthz` | `C-DATA-003` | Done (`fabfa89`) |
| 4 | Uploads staged in `TMPDIR` rather than a hard-coded `/tmp` | `C-RUNTIME-002` | Done (`fabfa89`) |
| 5 | Build workflows publish digest records (digest, commit, ref) for every pushed image | `C-RELEASE-001` | Done (`fabfa89`) |
| 6 | Day boundaries, savings and forecasts follow `TZ` / `/etc/localtime` | `C-TIME-001` | Done (earlier on the same branch) |
| 7 | Show host clock-sync state on the dashboard (input interface defined by `D-TIME-001`) | `C-TIME-001` | Open |
| 8 | Authenticated appliance-settings workflow for SSH enablement and device-generated keypair handling | `C-ACCESS-001` | Open (blocked on `D-ACCESS-001`) |
| 9 | Application-update controls/status that cannot activate an untrusted, dev-channel or replayed manifest | `C-UPDATE-004`, `C-RELEASE-001` | Open (blocked on `D-UPDATE-001`) |

These are application changes, not reasons to embed the Node.js application into the OS image build.

The conditional read-only-container-root requirement in `C-RUNTIME-002` is intentionally retained as a tightening beyond v0.5: once Stage 2 proves Epilykos has no required writes outside mapped persistent/temporary paths (`T-RUNTIME-003`), the production container SHOULD make its own root filesystem read-only as an independent containment layer.

## 10. Evidence directory convention

Implementation evidence SHOULD be committed or uploaded under a stable structure:

```text
evidence/
├── stage-0/
│   ├── boot-chain-spike.json
│   └── build-system-decision.md
├── stage-1/
│   ├── provisioning-ethernet.json
│   ├── provisioning-ap.json
│   └── provisioning-usb.json
├── stage-2/
│   ├── podman-info.json
│   ├── offline-boot.json
│   ├── readonly-root.json
│   └── release-provenance.json
├── stage-3/
│   └── ble-privilege-spike.json
├── stage-4/
│   ├── partition-map.txt
│   ├── power-cut-test.json
│   ├── boot-health.json
│   ├── uboot-env-corruption-recovery.json
│   └── ssh-access-policy.json
└── stage-5/
    ├── rauc-rollback.json
    ├── app-rollback.json
    ├── app-manifest-trust.json
    └── app-manifest-replay.json
```

A reviewer should be able to trace every release-blocking contract to concrete evidence without relying on prose claims.

## 11. Definition of v1 architecture-complete

The architecture is ready for implementation when all Stage 0 decisions are frozen where required, the complete later-stage decision backlog has been reviewed and assigned due stages, and this document plus `epilykos-os-contracts.yaml` are accepted as the source of truth. From that point onward, narrative architecture documents are explanatory; a code change that conflicts with a contract must either change the contract through review or be rejected.

## 12. Changelog

{{CHANGELOG}}
