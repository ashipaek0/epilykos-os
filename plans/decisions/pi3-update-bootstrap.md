# Pi 3B protocol-2 bootstrap state

This implements the first part of [OS update task 1](../pending/os-dashboard-updates.md).
The [approved design](os-dashboard-updates-design.md) remains authoritative.
Track B adds no prerequisite here. D1–D3 are unchanged.

## Delivery status

The portable native-record guard in
`os/board/epilykos/raspberrypi3/platform-env/` implements selection, capacity
checks and replicated commits. `make test` compiles it with warnings as errors
and runs real file-backed native-record fault tests. The POSIX driver is test
code and is never installed on a Pi.

The native integration adds pinned U-Boot 2025.10 and fw-tools 2026.04 integration,
Buildroot hooks and `make test-native-env`. It compiles the actual Pi3 ARM64
import/save paths and exercises real native fw-tools and RAUC 1.15.2
bootchooser commands against temporary files, with injected I/O failures.

**Not yet active in shipping images:** the paired Buildroot switch
`BR2_EPILYKOS_NATIVE_ENV_GUARD` defaults off, preserving current protocol-1
behavior. Task 1 is incomplete. Do not set `boot_protocol=2`, enable
installation or claim the invariant is enforced on a device until seeded
state, the checked boot script and the other requirements below are delivered.
Buildroot checks paired loader/fw-tools enablement after configuration and
again before building, and refreshes the shared source before each native
build. A stale cached loader configuration is refused; reconfigure the
packages when changing profiles rather than bypassing that check.

## Native record ABI

The existing copies stay at byte offsets `0x400000` and `0x404000`, each
`0x4000` bytes. A record has a 4-byte little-endian CRC32 over all payload
bytes, one incremental native flag byte, then NUL-terminated `name=value`
entries and a final extra NUL. Padding is covered by the CRC but is not
interpreted as entries. A record must have at most 256 distinct entries;
duplicate names, unterminated entries and non-printable values are refused.

Required critical state:

| Variable | Representation |
|---|---|
| `epk_state_schema` | Exactly `1` |
| `epk_state_generation` | Canonical unsigned decimal, at most `UINT64_MAX` |
| `epk_os_floor` | Same integer representation; zero only when explicitly seeded |
| `epk_trust_epoch` | Same integer representation; must also match the signed policy when authorized |
| `epk_trust_policy` | Canonical padded base64, 1–6144 decoded bytes |
| `epk_trust_policy_sig` | Canonical padded base64, exactly 64 decoded bytes |

All `epk_*` fields, including future transaction/confirmed-identity fields,
participate in critical equality and generation updates. This core does not
interpret a job's phase or authorize a caller to change it: the transaction
coordinator must enforce those semantics. BOOT counters/order and machine ID
are non-critical for generation purposes. Counters still use the same locked
selection/write path; they may not bypass the core.

The core validates framing and monotonic storage, **not signatures or JSON**.
Policy authorization must verify the pinned offline root, board/channel,
schema and scalar epoch match before submitting updates. Package admission
must verify that current policy again. CRC validity is not authenticity.

## Selection and commit

`epk_env_select()` checks both native CRCs. A CRC-valid record with malformed
platform state stops selection, even if the other copy parses. When only one
CRC is valid, its state is retained. Neither valid means refusal, never
initialization from defaults.

The greater critical generation wins only if floor and trust epoch do not
decrease relative to the other valid record. A policy/signature change at
the same epoch is a conflict. Equal generations require identical critical
fields, independent of entry order; only then do native incremental flags
choose counter/order differences, including the 255→0 wrap.

`epk_env_commit()` re-reads both copies through its backend, applies a bounded
batch and advances generation once if any critical field actually changes.
Callers cannot directly assign generation. At `UINT64_MAX`, further critical
changes are refused; ordinary counter refresh and repair remain possible.
Removing required state or lowering a floor/epoch writes nothing.

Every commit, including a no-op repair, writes the non-selected copy first,
flushes and checks exact readback, then writes/flushed-checks the selected
copy. It finally reads both again and checks CRC, expected flags and payload
before acknowledging. A backend error can occur after a new generation is
durable: return to selection/reconciliation, never retry a stale snapshot.
This implementation deliberately replicates counter-only writes too; measure
the additional SD writes in target acceptance before changing that policy.

The native adapter must hold the common fw-env exclusion from before the
first read through final verification. The portable core cannot enforce an
OS lock. The patched native CLI holds `/run/fw_printenv.lock` across import,
mutation and replication; machine-ID/health/RAUC CLI users share that lock.
Only the fixed redundant Pi3 MMC layout is supported when guarded: no MTD,
UBI, singular-env or temporary-file fallback. Native writes report short I/O,
fsync and readback errors. A no-op still replicates. An invalid/truncated
batch or failed single-variable write cannot commit a successful prefix or
return success.

`epk_env_commit_snapshot()` adapts native exports to the same core. It
refuses stale/explicitly changed generations, derives the bounded diff and
advances generation once for a critical batch. The lock spans the original
import and this commit; a snapshot is not a substitute for exclusion.

The U-Boot patch selects critical state during redundant import before any
loader saveenv. Its MMC save path uses aligned SD buffers and the same
snapshot commit. `epk_env_guard` tests compiled import readiness, not a mutable
environment flag. Defaults/import/save failures clear readiness; failed saves
require a reload/reboot before reuse. A successful save updates the RAM
generation for subsequent saves. Guarded `env erase` is refused. The later
protocol-2 boot script must check both readiness and save results before
launch; the current protocol-1 script is not advertised as meeting that rule.

This protects acknowledged state under the documented power-failure model.
It is not anti-rollback against root access, physical modification or restoring
an old whole-card image. Both-copy loss remains a repair condition.

## Capacity evidence and remaining integration

The 16384-byte record has 16379 payload bytes. At least 2048 bytes must remain
after the final NUL. The native tests accept the exact boundary and reject
one additional byte without any write. A fixture with the required scalars,
three BOOT variables, a full 6144-byte policy (8192 base64 bytes) and 64-byte
signature (88 base64 bytes) uses 8440 payload bytes, leaving 7939. This is an
ABI measurement, **not** the final target budget: actual default U-Boot
variables and worst-case pending/confirmed identities must be counted when
the protocol-2 environment is generated.

`make test-native-env` verifies pinned source hashes, applies both package
patches without fuzz, executes the actual Buildroot copy/paired-option hooks,
builds native fw-tools, cross-compiles guarded Pi3 import/save/core objects,
and exercises real RAUC `mark-good`/`mark-active` through those writers. It
tests native locking, counter/no-op repair, conflicts, torn first/second
writes, failed fsync, lying writes, aborted batches and loss of the newest
copy after an acknowledged trust/floor commit. The same sources are compiled
with the guard off and checked for protocol-1 compatibility. CI runs this as
a separate job alongside `make test contracts`.

This is **not** a full firmware/image build, running loader, D-Bus install
transaction or physical power-cut proof. The real RAUC test uses its minimal
local CLI build, with bundle/network/daemon features disabled; production
service transactions still belong to task 6.

ARM64 GCC 15 compile evidence: the snapshot function has a 62720-byte frame,
the commit helper 28928, MMC save 16560 and aligned I/O callbacks about 16528.
Those nested frames alone approach 122 KiB, excluding driver calls. The full
loader stack budget and real boots remain acceptance work; a host test does
not establish that budget on hardware. These are observations for the pinned
test toolchain, not universal compiler-independent limits.

Before task 1 can be completed:

- Complete the full image build and execute the patched loader on the Pi;
  object compilation and real Linux native-tool tests are not boot evidence.
- Integrate the host-generated authenticated seed artifacts with the full
  image, pinned BOOT public root and finalized runtime transaction budget.
- Implement authenticated source export and spare-card adoption with
  same-channel app floor preservation; database-only restore cannot enroll it.
- Execute the manual power-cut acceptance runner and record real Pi evidence.

Upstream interfaces checked: [U-Boot redundant import](https://github.com/u-boot/u-boot/blob/v2025.10/env/common.c),
[native fw-env implementation](https://github.com/u-boot/u-boot/blob/v2026.04/tools/env/fw_env.c),
[Buildroot uboot-tools version](https://github.com/buildroot/buildroot/blob/2026.05.3/package/uboot-tools/uboot-tools.mk).

## Host bootstrap artifacts and migration preflight

`tools/os_bootstrap.py` creates a **new directory of first-flash artifacts**:
`env-a.bin`, `env-b.bin`, public policy/signature/root, immutable platform
metadata and `budget.json`. It verifies the exact policy bytes with OpenSSL
Ed25519 before producing either record. It never opens a card/device, replaces
an existing directory or initializes missing live state. The trusted build
operator supplies the offline root public key; accepting a root supplied by a
candidate policy would not authenticate anything. Pinning that root into BOOT
and wiring these artifacts into the image remain task 2/4 integration.

The policy's concrete host-side schema is now:

- Exactly `schema: 1`, `board: "raspberrypi3"`, `channel: "dev"|"stable"`,
  positive uint64 `trust_epoch`, `index_keys`, `rauc_cas` and
  `rauc_leaf_spki_sha256`. Unknown fields are refused until a schema change.
- `index_keys`: 1–8 objects with exactly `key_id` (1–64 ASCII letters/digits,
  dot, underscore or hyphen, starting with a letter/digit) and `public_key`
  (canonical padded base64 of a raw 32-byte Ed25519 public key). IDs and
  public keys are unique.
- `rauc_cas`: 1–4 unique PEM strings, exactly one certificate per string,
  with X.509 CA basic constraints. `rauc_leaf_spki_sha256`: 1–16 unique
  lowercase hex SHA256 fingerprints. This validates public policy material;
  it does **not** verify a bundle's CMS chain, effective signer or expiry.
- Canonical JSON means sorted object keys, compact separators, ASCII JSON
  escapes, no NaN/Infinity, no duplicate keys, no trailing newline; arrays
  retain their signed order. Sign those exact bytes. Policy cap: 6144 bytes;
  detached raw signature: exactly 64 bytes. Root input: one Ed25519 PUBLIC
  KEY PEM block, with no private key or other trailing material.
- A verified current-policy context rejects older epochs and any different
  bytes at the same epoch. This host helper is not yet the runtime shared
  trust/revocation admission implementation required in task 4.

Seed generation uses the actual `u-boot-initial-env` generated by the pinned
configured loader, not a hand-maintained copy. It rejects duplicate or
reserved `epk_*`/`BOOT_*` defaults. Floor is explicitly the image's positive
OS sequence, generation is initially zero, both slots have three attempts,
and copies have identical state with incremental flags 1/2. No confirmed
health or successful installation is manufactured by seeding.

The worst-case budget reserves maximum uint64 scalars, a full 6144-byte
policy and 64-byte signature, plus **4096 aggregate serialized bytes and 64
entry slots** for later machine-ID/boot/runtime/pending/confirmed variables.
That reservation includes variable names, `=`, terminators and values. Future
transaction/loader code must fit it; it is not a finalized transaction ABI.
The native guard still refuses any actual record that violates headroom.
For the pinned generated Pi3 defaults (533 text bytes; SHA256
`1662c321ecdb3ff22b11a24ce98967b8fed91d3ee4d286a12a09472afb21fe83`),
the worst-case payload uses **13125 bytes**, leaving **3254 bytes** out of
16379, above the required 2048. `make test-native-env` regenerates defaults
and measures this budget, so a changed configuration cannot rely on this old
measurement. It also runs generated records through the compiled C guard.
This is a generated configuration budget, not final full-image/physical proof.

`tools/bootstrap_export.py` now supports a bounded live SSH export from an
**explicitly authorized protocol-1 source with an identifiable pre-0.7 OS**.
It uses an explicit verified known-hosts file, no automatic host-key acceptance,
key-only authentication and the existing source app maintenance lock. The
lock stays held until the client fsyncs and acknowledges its audit receipt.
Missing/malformed promoted floors, conflicting channel/active/current state,
active jobs, duplicate metadata and unsafe state files are refused. A pristine
source exports its immutable app baseline. A legacy source's OS floor and
trust epoch are `null`, never invented as zero. Protocol-2 sources and unknown
OS versions are deliberately refused until their immutable state/trust reader
exists; losing protocol-2 state cannot fall through into legacy enrollment.

An optional target `image.env` produces a baseline-adoption **plan** only:
same channel, target sequence at least the source app floor, identical app
identity when sequences are equal. Retaining a newer source OCI is not yet
implemented; use a sufficiently new baseline. The receipt is audit evidence,
not an offline enrollment credential. There is no JSON/database-backup import
command. **Actual spare-card installation/binding, protocol-2 source export,
first-boot adoption enforcement, database-only restore admission and factory
reset preservation remain unimplemented integration work.** The tool does
not claim that the existing dashboard restore path enforces these rules.

The read-only manual acceptance runner is documented with the pending physical
evidence in [Pi3 bootstrap evidence](../../evidence/os-updates/pi3-bootstrap.md).
