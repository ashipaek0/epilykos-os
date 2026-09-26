# Build-system decision (D-PLATFORM-001)

Part of `C-BOOT-000`'s evidence requirement: "written build-system
comparison including Home Assistant OS (Buildroot + RAUC) as prior art."
This is that comparison, not the hardware spike itself — `C-BOOT-000`
stays `blocked-on-spike` until real boot-chain evidence exists alongside
it (see `boot-chain-spike.json`, not yet populated).

## The candidates

| | Buildroot | Yocto |
|---|---|---|
| A/B + RAUC on Pi 3/4/5, ready-made | Yes — Home Assistant OS builds and maintains exactly this today | No — `meta-raspberrypi` + `meta-rauc` exist, but the bootchooser/slot layout for all three boards would be assembled here from scratch |
| Build model | A single `make`; one `.config`-style defconfig per board | Layered recipes/bitbake; more powerful for a large multi-product line, more machinery for one appliance image |
| Maintenance surface for one maintainer | Smaller — closer to "a kernel config and a package list" | Larger — recipe/layer versioning, bitbake cache management |
| Kernel/firmware update cadence, CVE response | Whatever cadence *we* commit to; no upstream OS releases to track | Same, unless tracking meta-raspberrypi's own release cadence instead |
| Prior art available to lean on | Home Assistant OS's public Buildroot config, for exactly this A/B-on-Pi problem | No comparably close public reference for this stack |

## Decision

Buildroot, following Home Assistant OS's own combination — Buildroot +
U-Boot + RAUC — across Pi 3, 4 and 5. This isn't a fork of HAOS's tree:
EpilykosOS has a different rootfs (Debian-family userspace choices differ
in places), a different partition layout (`C-BOOT-001`, still open on
exact sizes), and preloads a different set of application images
(Epilykos + the BMS bridge, not Home Assistant Core). What's reused is
the *combination of upstream pieces already proven on this exact
hardware range* — not writing our own Pi 4/5 board bring-up when a
maintained one already exists, which is the entire point of Tier 2 being
"best effort, relying on proven upstream board support" (`C-HW-001`).

## What this decision does not settle

- **D-BOOT-002** (confirm U-Boot + RAUC bootchooser from hardware
  evidence) — the scaffold at `os/board/epilykos/raspberrypi3/boot.cmd`
  implements the mechanism this decision points to, but no real boot has
  run it yet.
- **D-STORAGE-001** (partition sizes/filesystems) — `os/.../genimage.cfg`
  has placeholder sizes only.
- **D-RECOVERY-001** (a dedicated recovery target vs. a manual procedure)
  — `boot.cmd`'s "both slots exhausted" branch is a stopgap.
- Maintenance cost in practice (kernel/firmware update cadence, CVE
  response) is a claim to prove over time, not something a document like
  this can demonstrate on its own.

## What would overturn this

If Stage 0's actual build attempt on the Pi 3B shows Buildroot's Pi board
support is materially behind what `meta-raspberrypi` offers today (kernel
version, firmware, mainline DT overlays), or the RAUC U-Boot integration
doesn't hold up under a real forced-fallback test, that's grounds to
revisit — through review, recorded here, not by quietly switching the
scaffold under `os/`.
