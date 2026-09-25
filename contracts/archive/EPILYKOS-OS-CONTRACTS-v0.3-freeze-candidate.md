# EPILYKOS-OS-CONTRACTS

**Version:** 0.3-freeze-candidate  
**Architecture baseline:** *EpilykosOS: Appliance Architecture — v0.5*  
**Reference platform:** Raspberry Pi 5 (8 GB)  
**Scope:** Track 2 (EpilykosOS appliance) only. Track 1 (ordinary Docker Compose deployment) remains outside this contract.

> This document converts the v0.5 narrative architecture into implementation contracts and acceptance gates. A contract marked **decision-required** or **blocked-on-spike** is deliberately not silently completed: CI must treat it as unresolved until the named decision/evidence exists.

## 1. Normative language and contract states

**MUST** is release-blocking for the referenced stage. **SHOULD** requires an explicit documented exception if not followed. **MAY** is optional.

Contract states:

- `draft` — requirement is sufficiently defined to implement and test, but has not yet produced passing evidence.
- `decision-required` — a concrete value/scope decision remains open.
- `blocked-on-spike` — implementation depends on hardware evidence.
- `deferred-v1` — intentionally out of the v1 critical path.

The companion file `epilykosos-contracts-v0.3-freeze-candidate.yaml` is the machine-readable registry. CI should validate that every contract required by a stage is either `passed` or explicitly excluded by that stage's policy; unresolved contracts must never be interpreted as passing.

## 2. Frozen invariants

| ID | Invariant | Verification |
|---|---|---|
| `I-001` | Track 1 and Track 2 remain separate deployment products. | Repository documentation and release artifacts distinguish Docker Compose from EpilykosOS. |
| `I-002` | Developer and appliance EpilykosOS images use the same boot chain and partition topology. | Partition-table and boot-artifact comparison test. |
| `I-003` | EpilykosOS and Epilykos application updates are independent rollback domains. | Independent forced-failure rollback tests for RAUC and OCI planes. |
| `I-004` | The appliance boots and starts Epilykos with no Ethernet link, no configured Wi-Fi, and no Internet. | Offline boot acceptance test. |
| `I-005` | Persistent user data is never stored only inside ROOT-A or ROOT-B. | Filesystem scan plus A/B rollback persistence test. |
| `I-006` | Production appliance images MUST NOT use privileged=true for the BMS bridge unless a reviewed exception is recorded. | Static Quadlet scan and Stage 3 hardware test evidence. |
| `I-007` | Neither update plane may activate a candidate solely on unauthenticated metadata: OS bundles and application manifests both require an explicit trust decision before activation. | RAUC signature-rejection tests plus application-manifest trust/rejection tests. |
| `I-008` | Externally supplied application manifests are replay-protected by a monotonic release sequence stored on DATA; internal previous-known-good rollback remains independently available. | Application-manifest replay rejection plus internal rollback tests. |

## 3. Contracts

### C-BOOT-001 — Partition map

**Stage:** 0  
**Status:** `decision-required`

**Requirements**

- Both developer and appliance images MUST use GPT and the same ordered partition topology.
- Required labels are BOOT, ROOT-A, ROOT-B, DATA.
- RECOVERY is conditional and MUST NOT be added until D-RECOVERY-001 is resolved.
- ROOT-A and ROOT-B MUST be equal size.
- DATA MUST be a separate persistent filesystem and MUST survive OS slot switches and application rollbacks.
- Exact partition sizes are intentionally unresolved by architecture v0.5 and MUST be frozen before Stage 0 closes.

**Required evidence**

- parted -s <image> unit B print
- blkid output from first boot
- CI assertion that developer/appliance partition tables are identical

### C-BOOT-002 — U-Boot / RAUC bootchooser variables

**Stage:** 4  
**Status:** `draft`

**Requirements**

- Raspberry Pi firmware MUST boot U-Boot; U-Boot MUST select the boot slot before Linux starts.
- The bootchooser MUST use BOOT_ORDER and BOOT_<slot>_LEFT variables compatible with RAUC's U-Boot backend.
- RAUC in userspace MUST be the only normal runtime component that mutates slot-good/bad state.
- boot-attempts and boot-attempts-primary MUST be explicit configuration values; defaults are not acceptable.
- Loss or corruption of the U-Boot environment MUST have a documented deterministic recovery behavior.

**Unresolved before this contract can pass**

- Exact boot-attempts value
- Exact boot-attempts-primary value
- U-Boot environment storage location and redundancy strategy

**Required evidence**

- U-Boot environment dump before/after install
- Forced boot-failure test showing attempt decrement
- RAUC status output matched to U-Boot environment
- Corrupt/lost U-Boot environment recovery test

### C-BOOT-003 — Mark-good conditions

**Stage:** 4  
**Status:** `draft`

**Requirements**

- A new OS slot MUST be marked good only after multi-user userspace is reached, required filesystems are mounted, Podman is operational, and the Epilykos container is healthy.
- Network link, Internet reachability, BLE availability, inverter availability, and full SQLite integrity checks MUST NOT gate mark-good.
- A lightweight database open/schema operation MAY be part of the Epilykos container health check only to demonstrate application startup.
- Failure to satisfy boot health within the configured timeout MUST leave the slot unconfirmed; U-Boot attempt accounting then determines rollback.

**Unresolved before this contract can pass**

- Exact health-gate timeout

**Required evidence**

- systemd dependency graph
- epilykos-health-gate.service test
- Test with Ethernet unplugged and Wi-Fi unconfigured still marks slot good
- Test with intentionally broken Epilykos startup does not mark slot good

### C-DATA-001 — Persistent DATA paths

**Stage:** 0  
**Status:** `draft`

**Requirements**

- DATA MUST be the only authoritative persistent writable partition for application state.
- Epilykos application data MUST reside below /data/epilykos/.
- The current Epilykos database path ./data/energy.db MUST map to persistent DATA inside the container.
- Recommended host layout is /data/epilykos/app-data, /data/epilykos/snapshots, /data/epilykos/journal, /data/containers, and /data/epilykos/update-state.
- OS rollback MUST NOT revert database, settings, or snapshots.

**Required evidence**

- Mount table
- Container volume inspection
- Create data on ROOT-A, boot ROOT-B, verify identical data

### C-DATA-002 — Container storage paths

**Stage:** 2  
**Status:** `draft`

**Requirements**

- Rootless Podman persistent graph storage MUST be under /data/containers.
- Ephemeral Podman runtime state MUST remain under /run/user/<uid>/ and MAY be lost on reboot.
- OCI image storage MUST survive OS A/B slot switches.
- The appliance MUST NOT need to pull an OCI image during normal boot.

**Required evidence**

- podman info --format json
- Boot ROOT-A then ROOT-B with network disconnected and verify containers start

### C-DATA-003 — SQLite durability configuration

**Stage:** 4  
**Status:** `decision-required`

**Requirements**

- SQLite MUST remain in WAL mode unless a measured power-loss test justifies a different mode.
- The SQLite synchronous mode MUST be explicitly selected and recorded; architecture v0.5 does not freeze the value.
- Filesystem and mount options MUST NOT disable normal ext4 write barriers or otherwise trade durability for benchmark speed.
- Clean shutdown SHOULD checkpoint WAL, but correctness MUST NOT depend on clean shutdown.
- Power-cut testing MUST use the actual v1 storage medium and include repeated abrupt loss during active writes.
- Periodic integrity diagnostics MUST be separate from boot-health gating.

**Unresolved before this contract can pass**

- Exact PRAGMA synchronous value
- Periodic quick_check cadence

**Required evidence**

- PRAGMA journal_mode
- PRAGMA synchronous
- mount output
- Power-cut test report
- Snapshot restore test

### C-RUNTIME-001 — Rootless Podman host contract

**Stage:** 2  
**Status:** `draft`

**Requirements**

- Service account MUST be epilykos with a fixed UID/GID frozen in the image source.
- subuid/subgid ranges MUST be fixed at image build time.
- The host MUST use cgroup v2, crun, and netavark.
- The epilykos systemd user manager MUST start at boot without an interactive login.
- Required OCI images MUST be preloaded before first normal boot.
- Image pull behavior MUST be explicit and MUST NOT make boot network-dependent.

**Unresolved before this contract can pass**

- Fixed UID/GID numbers
- Fixed subuid/subgid ranges

**Required evidence**

- id epilykos
- /etc/subuid and /etc/subgid
- podman info
- loginctl show-user epilykos
- Offline boot test

### C-RUNTIME-002 — Epilykos Quadlet

**Stage:** 2  
**Status:** `draft`

**Requirements**

- The production Quadlet MUST reference an immutable image digest supplied by the active application manifest.
- The container MUST run as the epilykos service user or mapped rootless user, not as a privileged host process.
- Persistent /app/data MUST map to DATA.
- Application logs in appliance mode MUST go to stdout/stderr; the existing Winston file transport MUST be disabled or not configured.
- The container SHOULD use a read-only root filesystem once Stage 2 proves no required writes occur outside mapped persistent/temporary paths.
- Podman health MUST use a dedicated unauthenticated local health endpoint containing no secrets.
- Notify=healthy (or equivalent Quadlet readiness behavior) MUST gate systemd readiness.

**Current implementation gap**

- The current dev branch does not expose an obvious dedicated /health or /healthz endpoint; one must be added or explicitly identified.
- The current logger writes rotating files; appliance-mode stdout/stderr-only behavior requires an application switch or configuration change.

**Required evidence**

- Rendered systemd unit from Quadlet
- podman inspect
- Health endpoint test
- Filesystem write test

### C-RUNTIME-003 — BMS bridge Quadlet

**Stage:** 3  
**Status:** `blocked-on-spike`

**Requirements**

- privileged=true MUST NOT appear in the production appliance configuration without an approved exception.
- Capability/device access MUST be determined by the ordered Stage 3 spike, not guessed in advance.
- Test order: rootless + host BlueZ/D-Bus with no extra capabilities; narrow D-Bus policy; rootful-but-unprivileged; direct HCI capabilities only if necessary.
- The final D-Bus policy and device/capability set MUST be committed as code and regression-tested.

**Required evidence**

- Stage 3 BLE hardware test report
- Static Quadlet scan
- D-Bus policy file

### C-UPDATE-001 — OS update trust model

**Stage:** 5  
**Status:** `draft`

**Requirements**

- Every RAUC bundle MUST be signed.
- Development RAUC CA and Epilykos release RAUC CA MUST be separate trust anchors.
- Secure-boot keys MUST be a separate hierarchy from RAUC bundle-signing keys.
- Appliance images MUST trust the release RAUC CA; developer images MAY additionally trust a development CA according to documented policy.
- The private release signing key MUST NOT be embedded in images or repositories.
- Installation of an invalid, unsigned, or untrusted bundle MUST fail before writing an update slot.

**Required evidence**

- Valid signed-bundle install
- Invalid-signature rejection test
- Wrong-CA rejection test
- Repository secret scan

### C-UPDATE-002 — Application update manifest

**Stage:** 5  
**Status:** `draft`

**Requirements**

- Application manifests MUST pin immutable OCI digests; tags such as latest and dev MUST NOT be accepted by the appliance updater.
- Manifest MUST identify epilykos and bms-bridge independently.
- Updater state MUST distinguish current, candidate, and previous-known-good manifests.
- A candidate MUST be downloaded/verified before activation.
- Promotion MUST occur only after the candidate reaches application-health success.
- Failure MUST restore the previous-known-good manifest without changing the RAUC OS slot.
- The manifest MUST contain a monotonically increasing integer sequence used by C-UPDATE-004 for replay/downgrade protection.
- The human-readable release field is descriptive and MUST NOT be used as the anti-replay ordering primitive.

**Canonical manifest shape**

```yaml
schema: 1
release: string
images:
  epilykos:
    digest: sha256:<64 hex>
    source: string
  bms-bridge:
    digest: sha256:<64 hex>
    source: string
sequence: integer >= 1, monotonically increasing across promoted releases
```

**Required evidence**

- Schema validation test
- Reject-tag test
- Candidate failure rollback test
- OS slot unchanged assertion

### C-UPDATE-003 — Independent rollback behavior

**Stage:** 5  
**Status:** `draft`

**Requirements**

- OS update failure MUST roll back via U-Boot/RAUC without reverting DATA.
- Application update failure MUST roll back OCI manifests without changing the boot slot.
- A successful application update followed by an OS rollback MUST retain the active application manifest unless an explicit compatibility rule says otherwise.
- Compatibility metadata MAY later constrain OS/application combinations, but no hidden coupling is permitted.

**Required evidence**

- Four-way matrix test: OS success/fail x application success/fail
- DATA persistence assertion after each case

### C-UPDATE-004 — Application manifest delivery and trust

**Stage:** 5  
**Status:** `decision-required`

**Requirements**

- The appliance MUST NOT accept an application candidate merely because its OCI images are digest-pinned; the manifest authorizing those digests MUST itself arrive through an authenticated or explicitly user-approved trust path.
- Supported delivery paths MUST mirror the local-first OS-update model: USB/removable media, authenticated local upload/push, and an optional user-specified URL.
- The trust model for non-interactive manifest acceptance MUST be frozen before Stage 5 closes. Acceptable design families are: a cryptographically signed manifest verified against a pinned application-release trust anchor; or a pinned/verified source where every candidate requires explicit authenticated user approval. CI MUST NOT silently choose between these models.
- Transport security alone MUST NOT be treated as equivalent to release authorization unless the contract explicitly freezes that model and its pinning/approval semantics.
- An untrusted, unsigned, incorrectly signed, substituted, or otherwise unauthorized manifest MUST be rejected before any candidate OCI image is activated.
- Manifest verification MUST occur before C-UPDATE-002 candidate promotion logic begins.
- If application-manifest signing is selected, its trust anchor SHOULD be separate from the RAUC release CA so the two update planes remain independently revocable.
- Every externally supplied application manifest MUST carry a monotonically increasing integer release sequence in addition to the human-readable release/version string.
- The manifest trust decision MUST cover the release sequence together with the image digests; the sequence MUST NOT be accepted from unsigned/unapproved metadata beside an otherwise trusted manifest.
- The appliance MUST persist the highest promoted release sequence on DATA and MUST reject externally supplied manifests whose sequence is less than or equal to that floor as stale/replayed, unless an explicit authenticated downgrade procedure is invoked.
- Automatic rollback to the internally retained previous-known-good manifest is not treated as accepting an old external manifest and MUST remain possible without lowering the stored release-sequence floor.
- A failed candidate that is never promoted MUST NOT advance the stored highest promoted release sequence.

**Unresolved before this contract can pass**

- D-UPDATE-001: freeze application-manifest authenticity model and trust-anchor/source policy
- Whether user-specified URL delivery is in v1 or remains Stage 6 nice-to-have
- Whether v1 exposes an explicit authenticated operator downgrade override; default external-manifest behavior remains reject-on-sequence-replay.

**Required evidence**

- USB manifest delivery test
- Authenticated local upload/push test
- User-specified URL test if that path is enabled
- Tampered/substituted manifest rejection test
- Wrong-signer or untrusted-source rejection test
- Audit record showing source, trust result, release identifier, and selected digests without leaking secrets
- Valid old-manifest replay test: previously trusted manifest with sequence <= stored floor is rejected before OCI activation
- Failed-candidate test confirming an unpromoted higher sequence does not advance the stored release floor
- Internal previous-known-good rollback test confirming rollback remains possible without lowering the stored release floor

### C-NET-001 — Offline-boot invariant

**Stage:** 2  
**Status:** `draft`

**Requirements**

- Boot MUST reach healthy Epilykos application state with Ethernet unplugged, Wi-Fi unconfigured, DNS unavailable, and Internet unavailable.
- No service required for local inverter/BMS monitoring may have a hard dependency on network-online.target.
- Loss of network after boot MUST NOT stop local polling or corrupt persisted data.

**Required evidence**

- Network-isolated cold boot
- systemd critical-chain inspection
- Network pull/reconnect test

### C-PROV-001 — Ethernet provisioning

**Stage:** 1  
**Status:** `draft`

**Requirements**

- On first boot with DHCP Ethernet, the appliance MUST become discoverable by mDNS as epilykos.local.
- Provisioning MUST NOT require SSH or a terminal.
- Provisioning MUST work without vendor cloud connectivity.

**Required evidence**

- Clean-flash Ethernet-only test from a separate client

### C-PROV-002 — Wi-Fi AP provisioning

**Stage:** 1  
**Status:** `draft`

**Requirements**

- If no usable network is configured, a temporary provisioning AP/captive portal MUST be available according to the first-boot state machine.
- Re-entry after initial provisioning MUST require the defined physical recovery gesture.
- Provisioning credentials and secrets MUST not be written to logs or diagnostics bundles.

**Unresolved before this contract can pass**

- Exact physical/GPIO recovery gesture
- AP SSID naming and credential policy

**Required evidence**

- Clean-flash Wi-Fi-only provisioning test
- Recovery-gesture re-entry test
- Secret-redaction test

### C-PROV-003 — USB Ethernet gadget provisioning

**Stage:** 1  
**Status:** `draft`

**Requirements**

- Pi 5 USB gadget provisioning MUST expose a local network path sufficient to reach the provisioning UI.
- USB gadget provisioning MUST be available on first boot and after the physical recovery gesture.
- Documentation MUST warn that a host USB port may not supply sufficient power for a Pi 5.
- Failure of USB gadget setup MUST NOT prevent normal Ethernet/Wi-Fi provisioning.

**Unresolved before this contract can pass**

- Minimum supported host OS matrix

**Required evidence**

- Clean-flash USB-only provisioning test on supported host OSes
- Recovery-gesture USB re-entry test

### C-RECOVERY-001 — Slot exhaustion behavior

**Stage:** 5  
**Status:** `decision-required`

**Requirements**

- Behavior when both ROOT-A and ROOT-B are unbootable MUST be deterministic and tested.
- If a RECOVERY boot target is retained, U-Boot MUST select it; the normal initramfs MUST NOT own slot-selection policy.
- If RECOVERY is deferred from v1, the v1 manual recovery procedure MUST be documented and tested before release.

**Unresolved before this contract can pass**

- D-RECOVERY-001: include dedicated recovery partition in v1 or defer

**Required evidence**

- Both-slots-bad hardware test
- Recovery/reflash runbook validation

### C-ACCESS-001 — SSH maintenance access

**Stage:** 4  
**Status:** `decision-required`

**Requirements**

- SSH MUST be disabled by default in the appliance image.
- Developer-image SSH policy MAY differ, but MUST be explicit and MUST NOT weaken the appliance default.
- Enabling SSH on an appliance MUST require an explicit authenticated administrator action through the local Epilykos settings workflow; provisioning MUST NOT require SSH.
- When SSH is enabled through the appliance dashboard, the appliance MUST generate the SSH keypair as part of that workflow rather than requiring the installer to pre-supply a public key.
- Password authentication MUST remain disabled and root login MUST remain disabled.
- Generated private-key material MUST NOT appear in logs, diagnostics bundles, backups, telemetry, or application manifests.
- The key lifecycle — private-key delivery/export, persistent storage if any, rotation, revocation, and behavior when SSH is disabled again — MUST be frozen before Stage 4 closes.

**Unresolved before this contract can pass**

- D-ACCESS-001: freeze generated-key private-key delivery/storage, rotation, revocation, and disable semantics

**Required evidence**

- Fresh appliance image test showing SSH unavailable by default
- Authenticated settings workflow test enabling SSH and generating a keypair
- Key-only non-root login success test
- Password login and root login rejection tests
- Disable/re-enable lifecycle test
- Secret-redaction test covering generated key material

### C-TIME-001 — Clock and synchronization

**Stage:** 1  
**Status:** `draft`

**Requirements**

- The Pi 5 onboard RTC is the reference local clock source.
- The selected NTP client MUST be explicitly included and configured; it may not be assumed from distro defaults.
- Loss of Internet MUST NOT block boot.
- The dashboard MUST expose unsynchronized-clock state and provide a manual correction path.

**Unresolved before this contract can pass**

- Exact NTP implementation and server policy

**Required evidence**

- Cold boot without network
- NTP recovery test
- RTC retention test when backup battery is fitted

### C-LOG-001 — Logging and diagnostics

**Stage:** 4  
**Status:** `draft`

**Requirements**

- Appliance-mode container logs MUST use stdout/stderr and be collected by journald.
- Persistent journal storage MUST live on DATA with a hard size cap.
- Diagnostics export MUST include journal, container status, RAUC status, boot-slot metadata, kernel version, hardware enumeration, redacted network state, application version/digests, and database status.
- Diagnostics export MUST redact credentials, tokens, private keys, Wi-Fi PSKs, and application secrets.

**Unresolved before this contract can pass**

- Exact journal size cap

**Required evidence**

- Journal persistence across A/B switch
- Storage-cap test
- Diagnostics bundle content test
- Secret-redaction test

### C-SECUREBOOT-001 — Optional verified-boot chain

**Stage:** 6  
**Status:** `deferred-v1`

**Requirements**

- No v1 documentation may claim full secure boot merely because Pi OTP verifies U-Boot.
- If implemented, the chain MUST cover Pi ROM/EEPROM trust → verified U-Boot → U-Boot-verified kernel/DTB/initramfs.
- Root filesystem integrity, if required, MUST be treated as a separate design problem such as dm-verity.
- Secure boot remains optional and MUST NOT be required for RAUC bundle authenticity.

**Required evidence**

- Verified-boot negative test with tampered kernel/DTB/initramfs

## 4. Acceptance matrix

| Test ID | Stage | Assertion |
|---|---:|---|
| `T-BOOT-001` | 4 | Developer and appliance images expose identical partition labels/order and boot through U-Boot. |
| `T-BOOT-002` | 5 | A deliberately broken candidate OS exhausts attempts and returns to the previous good slot. |
| `T-BOOT-003` | 4 | No network link does not prevent mark-good when host/application boot health is otherwise satisfied. |
| `T-DATA-001` | 4 | Database/settings written on one slot remain unchanged after booting the other slot. |
| `T-DATA-002` | 4 | Repeated abrupt power cuts during writes do not produce unacceptable corruption on the selected v1 storage hardware. |
| `T-RUNTIME-001` | 2 | Cold boot with all networking unavailable starts Epilykos from preloaded OCI images. |
| `T-RUNTIME-002` | 2 | Epilykos container health failure prevents systemd readiness/RAUC confirmation. |
| `T-BLE-001` | 3 | BMS bridge communicates with test hardware without privileged=true, or an approved exception exists. |
| `T-UPDATE-001` | 5 | Official signed RAUC bundle installs; invalid and wrong-CA bundles are rejected. |
| `T-UPDATE-002` | 5 | Broken application candidate rolls back to previous digest without switching OS slot. |
| `T-UPDATE-003` | 5 | OS rollback preserves DATA and active application-update state according to C-UPDATE-003. |
| `T-PROV-001` | 1 | Fresh image is provisionable over Ethernet without terminal access. |
| `T-PROV-002` | 1 | Fresh image is provisionable through temporary Wi-Fi AP/captive portal. |
| `T-PROV-003` | 1 | Fresh image is provisionable over USB Ethernet gadget; recovery gesture can re-enter this mode. |
| `T-LOG-001` | 4 | Diagnostics bundle contains required evidence and no configured secrets. |
| `T-RECOVERY-001` | 5 | Both-slots-bad behavior matches the resolved recovery contract. |
| `T-BOOT-004` | 4 | Corrupt or remove the U-Boot bootchooser environment and verify the documented deterministic recovery behavior without silently booting an arbitrary slot. |
| `T-UPDATE-004` | 5 | A substituted/tampered or unauthorized application manifest is rejected before candidate OCI activation; a trusted/approved manifest proceeds to C-UPDATE-002 health/promotion logic. |
| `T-ACCESS-001` | 4 | Fresh appliance has SSH disabled; authenticated dashboard enablement generates the appliance keypair, permits key-only non-root login, rejects password/root login, and does not leak private-key material. |
| `T-UPDATE-005` | 5 | After promoting application sequence N, re-present a previously trusted/approved manifest with sequence <= N and verify rejection before OCI activation; then verify internal previous-known-good rollback still works without lowering the stored floor. |

### Test-fixture reuse

`C-NET-001`, `C-DATA-002`, and `T-RUNTIME-001` deliberately overlap around an offline cold boot. They verify different properties — absence of network dependency, persistence of OCI storage across slots, and successful runtime startup — but CI SHOULD use one shared network-isolated boot fixture and record separate assertions/evidence instead of creating unrelated test rigs.

## 5. Stage gates

A stage is complete only when every test assigned to that stage passes and every contract needed by that stage is no longer `decision-required` or `blocked-on-spike`, except where the roadmap explicitly marks the feature deferred.

| Stage | Gate |
|---:|---|
| 0 | Partition topology, update-domain split, reference hardware and v1 recovery scope are frozen where due; the complete decision backlog is reviewed and every open decision has an explicit due stage. |
| 1 | Ethernet, Wi-Fi AP and USB provisioning paths work from a clean flash without terminal access. |
| 2 | Rootless Podman host contract is proven; Epilykos starts from preloaded images with no network present. |
| 3 | Serial/RS232/RS485 and BLE hardware tests pass; BMS bridge privilege set is evidence-based and `privileged=true` is absent unless exception-approved. |
| 4 | Appliance policy is active: read-only root, persistent DATA, logging policy, SSH default-off/key lifecycle, U-Boot/RAUC bootchooser, corrupted-environment recovery, and boot-health separation all pass hardware tests. |
| 5 | Signed RAUC updates and trusted, replay-protected, digest-pinned OCI application updates both pass independent authenticity, success/failure, and rollback tests. |
| 6 | Optional verified boot and user-specified update URL are implemented only if separately approved. |

## 6. Decision governance

The decision registry is **not** ordered by when a decision was introduced. It is ordered by `due_stage`, then decision ID. A decision added in a later contracts revision has exactly the same review and release-gating semantics as one present from the first pass.

Stage 0 must review the **entire** open decision backlog and confirm each decision's due stage. Only decisions due at Stage 0 have to be resolved before Stage 0 closes; later-stage decisions remain open but visible. A stage cannot close while an open decision whose `due_stage` is that stage or earlier remains unresolved.

This explicitly applies to the two decisions introduced in v0.2:

- `D-ACCESS-001` — due before Stage 4 closes.
- `D-UPDATE-001` — due before Stage 5 closes.

## 7. Decisions that must not be guessed

| Decision ID | Due stage | Status | Blocks | Question |
|---|---:|---|---|---|
| `D-RECOVERY-001` | 0 | `open` | `C-RECOVERY-001`, `C-BOOT-001` | Include a dedicated recovery boot target in v1 or defer it and freeze a manual recovery procedure. |
| `D-STORAGE-001` | 0 | `open` | `C-BOOT-001` | Freeze exact BOOT/ROOT-A/ROOT-B/DATA sizes and filesystem types. |
| `D-PROV-001` | 1 | `open` | `C-PROV-002`, `C-PROV-003` | Define the physical recovery gesture and USB host support matrix. |
| `D-TIME-001` | 1 | `open` | `C-TIME-001` | Choose and configure the NTP implementation/server policy. |
| `D-RUNTIME-001` | 2 | `open` | `C-RUNTIME-001` | Freeze epilykos UID/GID and subuid/subgid ranges. |
| `D-BLE-001` | 3 | `open` | `C-RUNTIME-003` | Run Stage 3 D-Bus/BlueZ spike and record minimum privilege set. |
| `D-ACCESS-001` | 4 | `open` | `C-ACCESS-001` | Freeze dashboard-generated SSH key lifecycle: private-key delivery/export, persistent storage if any, rotation, revocation, and disable/re-enable semantics. |
| `D-BOOT-001` | 4 | `open` | `C-BOOT-002` | Freeze U-Boot boot-attempt counts and environment storage/redundancy. |
| `D-LOG-001` | 4 | `open` | `C-LOG-001` | Freeze persistent journal storage cap. |
| `D-SQLITE-001` | 4 | `open` | `C-DATA-003` | Freeze SQLite synchronous mode and periodic quick_check cadence. |
| `D-UPDATE-001` | 5 | `open` | `C-UPDATE-004` | Freeze the application-manifest authenticity model: signed manifest with pinned application-release trust anchor, or pinned/verified source plus authenticated per-candidate user approval; define v1 delivery paths. |

## 8. CI contract

The repository SHOULD provide a single contract-validation entry point, for example:

```text
make contracts
```

That target MUST fail when:

- the machine-readable registry is syntactically invalid;
- a contract ID, acceptance-test ID, or decision ID is duplicated;
- any decision lacks `due_stage`, `status`, or Stage-0 review visibility;
- a stage marked releasable still contains an open decision whose `due_stage` is that stage or earlier;
- production Quadlets contain `privileged=true` without an approved exception marker;
- production application manifests use mutable OCI tags instead of `sha256:` digests;
- an application candidate can be activated without satisfying `C-UPDATE-004` manifest trust;
- an externally supplied application manifest with sequence at or below the stored promoted-release floor can be activated without an explicit authenticated downgrade exception;
- appliance SSH is enabled by default or permits password/root login;
- developer and appliance partition topologies diverge;
- required appliance OCI images are not present in the image manifest/preload set.

Hardware-dependent tests MUST emit machine-readable evidence rather than being silently skipped. A permitted skip must name the missing hardware capability and must not count as a pass for a release gate.

## 9. Application changes required by the contract

The current Epilykos `dev` branch already uses `./data/energy.db` and containerized deployment, which aligns well with `C-DATA-001`. Appliance-facing changes required by the contracts include:

1. add or formally identify a dedicated local health endpoint suitable for Podman `HealthCmd=`; it must not expose secrets and should validate only application-startup health, not Internet/BLE/inverter availability;
2. make Winston's rotating file transport disableable so appliance mode can use stdout/stderr → journald as the single logging path;
3. provide an authenticated appliance-settings workflow for SSH enablement and device-generated keypair handling according to `C-ACCESS-001`;
4. expose application-update controls/status without allowing an untrusted or replayed manifest to reach candidate activation, per `C-UPDATE-004`.

These are application changes, not reasons to embed the Node.js application into Yocto.

The conditional read-only-container-root requirement in `C-RUNTIME-002` is intentionally retained as a tightening beyond v0.5: once Stage 2 proves Epilykos has no required writes outside mapped persistent/temporary paths, the production container SHOULD make its own root filesystem read-only as an independent containment layer.

## 10. Evidence directory convention

Implementation evidence SHOULD be committed or uploaded under a stable structure:

```text
evidence/
├── stage-1/
│   ├── provisioning-ethernet.json
│   ├── provisioning-ap.json
│   └── provisioning-usb.json
├── stage-2/
│   ├── podman-info.json
│   └── offline-boot.json
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

The architecture is ready for implementation when all Stage 0 decisions are frozen where required, the complete later-stage decision backlog has been reviewed and assigned due stages, and this document plus `epilykosos-contracts-v0.3-freeze-candidate.yaml` are accepted as the source of truth. From that point onward, narrative architecture documents are explanatory; a code change that conflicts with a contract must either change the contract through review or be rejected.
