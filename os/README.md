# os/ — the EpilykosOS image build

This is a Buildroot `BR2_EXTERNAL` tree, not a fork of Buildroot itself:
Buildroot stays upstream and unmodified; everything in here is board
config, the A/B boot chain, and the handful of scripts that stitch them
together into an SD card image.

**Status: first hardware boot found a root-device bug, now fixed;
unverified again on hardware.** The first image flashed to a real Pi 3B
got through the firmware and U-Boot and then panicked: `root=LABEL=`
only works with an initramfs, which this image doesn't have. The image
now boots `root=PARTUUID=` with a fixed disk signature, is 64-bit (the
application images are only published for amd64 and arm64), and carries
networking, Bluetooth and the Epilykos service. CI checks what it can
without hardware: the build, the image layout, and a boot through U-Boot
under QEMU. Proving the boot chain on the real board, with serial console
evidence of a slot switch and a forced fallback, is `C-BOOT-000` —
Stage 0's own gate, still open.

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

## What's in the image (Raspberry Pi 3B)

- AArch64 userland, mainline Linux 6.6 (arm64 `defconfig` trimmed to the
  BCM2837), U-Boot 2025.10, systemd, RAUC.
- SD card layout, four primary MBR partitions, disk signature
  `0x45504c4b`:

  | Partition | PARTUUID | Contents |
  |---|---|---|
  | (gap at 4 MiB) | — | U-Boot environment, two redundant copies |
  | p1 BOOT | `45504c4b-01` | Pi firmware, `u-boot.bin`, `boot.scr`, `Image`, DTB |
  | p2 ROOT-A | `45504c4b-02` | root filesystem, slot A |
  | p3 ROOT-B | `45504c4b-03` | root filesystem, slot B |
  | p4 DATA | `45504c4b-04` | `/data`, grown to fill the card on first boot |

- Networking: DHCP on wired Ethernet and Wi-Fi (Ethernet preferred when
  both are up), reachable as `epilykos.local` (mDNS). Wi-Fi is iwd; known
  networks are kept on DATA (`/data/iwd`) so they survive OS updates.
- Bluetooth: BlueZ with the onboard radio (its firmware and the mainline
  serdev attach on the PL011); the container uses it over D-Bus.
- Epilykos: the image pinned by `manifests/<channel>.yaml` is preloaded
  for arm64 and started by `epilykos.service`; the app is at
  `http://epilykos.local:3000`. Its data lives in
  `/data/epilykos/app-data`; Podman's storage in `/data/containers`.
- Boot health: `epilykos-mark-good.service` marks the slot good once
  `/data` is mounted, Podman works and the app's `/healthz` is healthy
  (`C-BOOT-003`). No network is needed for any of it.
- Console only, no SSH (`C-ACCESS-001`): log in on the serial header
  (GPIO 14/15, 115200 8N1) or HDMI + USB keyboard as `root`, password
  `epilykos`.

## Wi-Fi setup

Any one of these works; Ethernet needs no setup at all.

1. **A text file on the card.** After flashing, open the card's BOOT
   drive on your computer, copy `wifi-example.txt` to `wifi.txt`, and
   fill in `ssid=`, `password=` and `country=` (two-letter code, e.g. `NG`).
2. **Raspberry Pi Imager's settings.** Imager only offers its Wi-Fi
   settings for images from an OS list, not for "Use custom".
   `tools/imager_os_list.py` writes that list for a published image; open
   Imager with `rpi-imager --repo <list file or URL>`, pick EpilykosOS,
   and fill in Wi-Fi under "Customise". A classic `wpa_supplicant.conf`
   dropped on BOOT works too.
3. **From a phone over Bluetooth.** With the box powered and not yet on
   a network, open <https://www.improv-wifi.com> in Chrome (Android,
   Windows, macOS, ChromeOS), choose "Connect device to Wi-Fi", pick
   *Epilykos*, and enter the network. When it joins, the page links to
   the dashboard. This is the open Improv Wi-Fi protocol; Home Assistant
   discovers it too. iPhone Safari has no Web Bluetooth (the Bluefy
   browser does).

Files on BOOT are read once at boot (`epilykos-wifi-import`), saved as an
iwd network, and deleted from the card so the password doesn't stay
there. Imager's `firstrun.sh` is only read for its Wi-Fi settings, never
run. Bluetooth setup is offered whenever the box has no network address,
and for the first 30 minutes after each boot while no Wi-Fi is configured
(so a box on Ethernet can still be given Wi-Fi). Passwords are never
written to the journal (`C-PROV-002`).

On the console, `iwctl station wlan0 show` and `networkctl` show the
Wi-Fi state; `journalctl -u epilykos-wifi-import -u epilykos-improv`
shows what setup did.

## First boot

1. Firmware and U-Boot start; `boot.scr` prints
   `EpilykosOS: booting slot A` and starts Linux.
2. DATA grows to fill the card, then the preloaded application image is
   loaded into Podman's storage. On a Pi 3B this takes a few minutes; it
   only happens on the first boot of each slot.
3. Epilykos starts; once `/healthz` answers, the slot is marked good.

Useful commands on the console: `systemctl status epilykos`,
`journalctl -u epilykos -f`, `rauc status`, `fw_printenv`,
`bluetoothctl show`, `networkctl`.

## Layout

```text
os/
  external.desc, external.mk, Config.in   BR2_EXTERNAL boilerplate
  configs/
    epilykos_raspberrypi3_defconfig       Tier 1 board (tested)
  board/epilykos/raspberrypi3/
    config.txt                            Pi firmware config (64-bit, loads u-boot.bin)
    linux.fragment                        kernel config over arm64 defconfig
    uboot.fragment                        U-Boot env: raw, redundant, on the SD card
    boot.cmd                              RAUC slot selection (compiled to boot.scr)
    genimage.cfg                          SD card layout
    post-build.sh                         fstab; preloads the pinned app image
    post-image.sh                         copies + relabels slot B, runs genimage
    wifi-example.txt                      Wi-Fi template placed on BOOT
    rootfs-overlay/                       files copied onto the built rootfs:
                                            systemd units + presets, epilykos-run,
                                            RAUC system.conf, fw_env.config,
                                            network/mDNS, iwd, Podman storage,
                                            Wi-Fi setup (epilykos-wifi-import,
                                            epilykos-improv, python/epilykos_wifi.py)
  imager/icon.png                         icon for the Raspberry Pi Imager OS list
  build.sh                                fetches a pinned Buildroot release
                                           and builds against this tree
```

Units are enabled by `usr/lib/systemd/system-preset/50-epilykos.preset`,
not by `.wants` symlinks: Buildroot runs `systemctl preset-all` when it
assembles the rootfs, which removes symlinks the presets don't ask for.

## Building

Host tools this needs beyond a normal Buildroot machine (Debian/Ubuntu
package names): `genimage mtools dosfstools device-tree-compiler skopeo`.
On Debian/Ubuntu:

```bash
sudo apt install build-essential bc bison flex cpio unzip rsync file \
  wget git python3 libssl-dev libncurses-dev device-tree-compiler \
  genimage mtools dosfstools e2fsprogs skopeo
EPILYKOS_CHANNEL=dev ./build.sh raspberrypi3   # or stable
```

This is two Buildroot commands (`make epilykos_raspberrypi3_defconfig`,
`make`) against a pinned Buildroot release — read `build.sh` before running
it. It downloads several GB and commonly takes over an hour.
`EPILYKOS_CHANNEL` picks which application manifest is preloaded
(`manifests/dev.yaml` by default); the build pulls that digest's arm64
image from Docker Hub with skopeo.

**CI runs this build too** (`.github/workflows/os-build.yml`) — on demand
(Actions → OS image build → Run workflow, on any branch, including `dev`;
choose the channel there) and on every push to `main` that touches `os/`
or the stable manifest, which preloads the stable release. It's separate
from the fast structural lint that runs on pull requests
(`os-build-check.yml`) precisely because it's this expensive. A green run
means the image built, has the expected layout and contents, and (best
effort) booted through U-Boot to Linux under QEMU — **none of which is
`C-BOOT-000`**: that still needs a real Pi 3B, a real SD card, and a
serial console log of a slot switch and a forced fallback. If a
real-hardware boot fails, that failure is useful signal for `C-BOOT-000` —
please record it in `evidence/stage-0/`, not just fix it silently past
what the contract expects to be reviewed.

## Open decisions this depends on

- `D-STORAGE-001` (BOOT/ROOT-A/ROOT-B/DATA sizes) — `genimage.cfg`'s
  partition sizes are placeholders, not this decision.
- `C-BOOT-001` asks for GPT; the Pi 3B needs an MBR (or a hybrid MBR), so
  the image uses plain MBR until that is settled.
- `D-BOOT-001` (attempt counts, environment location) — `boot.cmd`,
  `uboot.fragment` and RAUC's `system.conf` implement a proposal (3
  attempts, raw redundant environment), not the decision.
- `D-RECOVERY-001` (a dedicated recovery target vs. a manual procedure) —
  when both slots run out of attempts, `boot.cmd` gives both their
  attempts back (RAUC's reference behaviour); that is a stopgap.
- `D-RUNTIME-001` — Podman runs rootful for now (the app inside the
  container is still uid 1000); rootless waits on this decision.
- `D-BOOT-002` — this tree implements what it asks to confirm, but the
  confirming evidence doesn't exist yet.
