# Complete Linux slots: artifact and loader evidence

This is a Task 2 implementation stage, not physical acceptance or an enabled
OS updater. The default image still uses the protocol-1 boot script. Neither
the new slot script nor the artifact tools are selected by post-build or
post-image yet. `BR2_EPILYKOS_NATIVE_ENV_GUARD` remains disabled by default.

## Implemented boundary

`tools/slot_linux.py` stages the fixed Image and Pi 3B DTB paths, immutable
platform JSON and a content manifest. Before staging it checks the AArch64
Image header/kernel release, DTB framing/board compatibility, the single
module-release directory, each module's ELF architecture and release in
`.modinfo`, and the platform's app/channel/OS baseline. The manifest binds
every regular module-tree file, including dependency indexes, and the exact
kernel, DTB, platform and image baseline bytes. Merged `/usr` layouts are
supported; escaping paths and module symlinks are refused.

`tools/check_slot_linux.py` checks a regular ext4 image with read-only
`e2fsck`/`debugfs`, extracts these files and the entire module parent, and
recomputes that identity. A stale additional module release is therefore
visible even if the build target directory has subsequently been repaired.
These are build tools, not authorization for an externally supplied update.
Matching release strings alone do not prove kernel/DTB/modules came from
the same build: the build-hook provenance binding is still required.

`boot-slot.cmd` is the experimental protocol-2 loader script. It:

- Requires a valid imported native environment and bounded pending job
  identity before saving. Job UUIDs use canonical UUIDv4; sequences use
  positive canonical uint64; target hashes use 64 lowercase hexadecimal
  characters. It rejects invalid slots, counters, phases and orphaned or
  conflicting trial state.
- Selects only ROOT-A (`mmc 0:2`) or ROOT-B (`mmc 0:3`), skips an unactivated
  `ready` target, and decrements the selected counter before either load.
- Saves the trial marker in the same checked replicated commit as that
  decrement, preserving confirmation phases. A failed save halts.
- Loads both Image and DTB from the selected ROOT's fixed paths. A missing
  file or returned `booti` resets with the attempt spent. It never falls
  back to a shared kernel or reuses an earlier DTB after a failed load.
- Halts with counters retained when no slot is eligible. Its native halt
  endpoint stops available watchdogs and never returns to bootflow; actual
  Pi watchdog stop/handoff behavior remains unverified.

## Native readback defect found by the loader test

The first uninstrumented sandbox test wrote a valid new environment record
but then read zero-filled data from the end of ROOT-A. Save verification
refused the mismatch and the script halted without launching Linux.

In pinned U-Boot 2025.10, the generic MMC environment GUID search ignores
the result of `uuid_str_to_bin()` and can compare uninitialized output for
an MBR partition with no type GUID. Stack contents can falsely identify
that partition as a GPT environment partition. The debugger observed LBA
83936, exactly fixture ROOT-A's start 18432 + length 65536 - 32 blocks,
instead of the fixed environment LBA. Diagnostic logging changed stack
contents and concealed the failure; it was removed before verification.

The guarded `mmc_get_env_addr()` path now uses the two fixed raw ABI
offsets, rejects invalid copy indexes, and refuses a compiled layout that
differs from that ABI. Generic DT/GPT hints cannot redirect a guarded
commit/readback. Guard-off behavior is unchanged. The sandbox deliberately
retains GPT type-GUID support and supplies conflicting DT offsets to test
this boundary.

## Reproducible checks

On a Linux build host:

```sh
python3 tools/test_slot_linux.py
make test-slot-boot
make test-native-env
make test contracts
```

The sandbox checker downloads the existing checksum-pinned U-Boot 2025.10
source and builds it afresh. In addition to a C toolchain, bison, flex, m4,
patch and OpenSSL headers, it needs SWIG, Python development headers,
setuptools, pyelftools and e2fsprogs. No physical block device is opened.

Ten artifact tests cover mutations of packed ext4 kernel bytes, stale
module releases, changed/missing/extra modules, wrong architecture/release,
malformed/wrong-board DTBs, platform binding, the packed runtime module alias and path escapes. Ten loader
tests execute real hush, native import/selection, replicated MMC saves and
ext4 reads against a disposable file-backed SD card. They cover both slot
identities, missing DTB despite stale RAM, save failure, trial persistence,
three failed attempts then fallback, exhausted counters, ready-target
exclusion, invalid/lost state, and malformed pending identities before save.

Only kernel execution and CPU reset use test endpoints. The normal kernel
endpoint exits immediately at launch so durable counters and trial state
are inspected before any later script command. A separate returned-kernel
case checks reset and spent attempts. A mutation moving saveenv after launch
is rejected by both the counter and trial persistence checks. Fixture kernels
and DTBs distinguish slot identity; they are never claimed to boot Linux.
The native integration checker separately compiles the real ARM64
import/save/core and boot-guard/halt objects. Host filesystem checks and
sandbox execution do not establish SD-card durability or Pi bootability.

## Still required before Task 2 acceptance

Wire same-build kernel/DTB/module provenance, platform metadata, authenticated
seed records and selected boot script into post-build/post-image. Check
both generated ROOT images and final card layout; repeat the generated
default/runtime environment budget. Complete the full Pi loader/image
build and real A/B boots, including failed candidate and rollback tests.
Task 3 must establish early watchdog reset, health supervision and confirmed
slot counter handling. No release gate may treat the checks above as
physical acceptance, and no protocol-2 shipping activation is enabled here.
