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

**Not yet active in an image:** neither U-Boot nor fw_printenv/fw_setenv is
patched to call this core. The current protocol-1 images and RAUC behavior
remain unchanged. This is a review boundary within task 1, not completion of
that task or an enabled OS update path. Do not set `boot_protocol=2`, enable
installation or claim the invariant is enforced on a device until both native
paths, seeded state and the other bootstrap requirements below are delivered.

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
OS lock. The test backend uses a shared file lock to exercise competing real
processes; this does not prove fw-env/RAUC integration yet. Native I/O adapters
must report short reads/writes, flush failures and readback errors; U-Boot's
backend must preserve the same callback contract without libc/POSIX calls.

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

Before task 1 can be completed:

- Apply this same core to the pinned U-Boot 2025.10 import/save path before
  any loader saveenv, and to Buildroot 2026.05.3's native uboot-tools 2026.04
  read/write path. Their versions differ; patch/test both explicitly.
- Exercise native fw-tools and actual RAUC mark-good/mark-active under the
  common lock. Pure C/file tests do not substitute for this.
- Generate and check seeded redundant records from authenticated bootstrap
  policy, fixed platform identity and the actual final environment budget.
- Implement authenticated source export and spare-card adoption with
  same-channel app floor preservation; database-only restore cannot enroll it.
- Deliver the manual power-cut acceptance runner and record real Pi evidence.

Upstream interfaces checked: [U-Boot redundant import](https://github.com/u-boot/u-boot/blob/v2025.10/env/common.c),
[native fw-env implementation](https://github.com/u-boot/u-boot/blob/v2026.04/tools/env/fw_env.c),
[Buildroot uboot-tools version](https://github.com/buildroot/buildroot/blob/2026.05.3/package/uboot-tools/uboot-tools.mk).
