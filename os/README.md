# os/ — the EpilykosOS image build

This is a Buildroot `BR2_EXTERNAL` tree, not a fork of Buildroot itself:
Buildroot stays upstream and unmodified; everything in here is board
config, the A/B boot chain, and the handful of scripts that stitch them
together into an SD card image.

**Status: untested scaffold.** Nothing in this directory has been run
against a real Buildroot checkout or real hardware yet. It exists so the
first real build attempt has something concrete to fail against, rather
than starting from nothing — see the caveats in each file before trusting
it. Proving it actually builds and boots on the Raspberry Pi 3B, with
serial console evidence of a slot switch and a forced fallback, is
`C-BOOT-000` — Stage 0's own gate, not yet closed.

## Why Buildroot + U-Boot + RAUC

Recorded by `D-PLATFORM-001`: [Home Assistant OS](https://github.com/home-assistant/operating-system)
already builds and maintains exactly this combination — Buildroot, U-Boot,
RAUC A/B updates — across Pi 3, 4 and 5. Tier 2 support in this project
(`C-HW-001`) is explicitly "best effort, relying on proven upstream board
support"; reusing an upstream project's board support for Pi 4/5 is what
makes that credible, instead of maintaining our own port of three boards
we don't all own. This tree is not a fork of HAOS's — it's the same
choice of upstream pieces, assembled for this appliance's own contract
(a different rootfs, a different set of preloaded application images, its
own partition layout).

## Layout

```text
os/
  external.desc, external.mk, Config.in   BR2_EXTERNAL boilerplate
  configs/
    epilykos_raspberrypi3_defconfig       Tier 1 board (tested)
  board/epilykos/raspberrypi3/
    config.txt                            Pi firmware config (loads u-boot.bin)
    linux.fragment                        kernel config additions over the
                                           mainline bcm2709 defconfig
    boot.cmd                              U-Boot script: RAUC bootchooser
                                           slot selection (compiled to boot.scr)
    uboot.env                             default U-Boot environment
    genimage.cfg                          SD card partition layout
    post-build.sh                         fstab, /data mountpoint
    post-image.sh                         duplicates+relabels the B slot,
                                           then runs genimage
    rootfs-overlay/                       files copied onto the built rootfs:
                                             - etc/rauc/system.conf
                                             - epilykos-data-init (format+grow
                                               DATA on first boot)
                                             - epilykos-mark-good (C-BOOT-003)
  build.sh                                fetches a pinned Buildroot release
                                           and builds against this tree
```

## Building

```bash
./build.sh raspberrypi3
```

This is two Buildroot commands (`make epilykos_raspberrypi3_defconfig`,
`make`) against a pinned Buildroot release — read `build.sh` before running
it. It downloads several GB and commonly takes over an hour; there's no
Pi hardware or that kind of disk/network budget in the environment this
scaffold was written in, so it has not actually been run. If it fails on
a real machine, that failure is useful signal for `C-BOOT-000` — please
record it in `evidence/stage-0/`, not just fix it silently past what the
contract expects to be reviewed.

## Open decisions this depends on

- `D-STORAGE-001` (BOOT/ROOT-A/ROOT-B/DATA sizes) — `genimage.cfg`'s
  partition sizes are placeholders, not this decision.
- `D-RECOVERY-001` (a dedicated recovery target vs. a manual procedure) —
  `boot.cmd`'s "both slots exhausted" branch is a stopgap, not that
  decision.
- `D-BOOT-002` — this tree implements what it asks to confirm, but the
  confirming evidence doesn't exist yet.
