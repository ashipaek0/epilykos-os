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

- Power cuts: ROOT-A and ROOT-B are mounted read-only and never written
  in normal operation; `/var` is a tmpfs. What has to survive a reboot
  lives on DATA, which is checked and repaired at boot: the app's data,
  Podman's storage, Wi-Fi networks, Bluetooth pairings, the last known
  time and the journal (64 MB cap, so `journalctl -b -1` shows the
  previous boot). The machine ID is kept in the U-Boot environment. Swap
  is 256 MB of compressed RAM (zram), never the SD card.
- Networking: DHCP on wired Ethernet and Wi-Fi (Ethernet preferred when
  both are up), reachable as `epilykos.local` (mDNS). Wi-Fi is iwd; known
  networks are kept on DATA (`/data/iwd`) so they survive OS updates.
- Bluetooth: BlueZ with the onboard radio (its firmware and the mainline
  serdev attach on the PL011); the container uses it over D-Bus.
- Epilykos: the image pinned by `manifests/<channel>.yaml` is preloaded
  for arm64 and started by `epilykos.service`; the app is at
  `http://epilykos.local:3000`, or on the port chosen in Epilykos
  settings (80 drops the `:port`; kept in `/data/epilykos/port`). Its data lives in
  `/data/epilykos/app-data`; Podman's storage in `/data/containers`.
- Boot health: `epilykos-mark-good.service` marks the slot good once
  `/data` is mounted, Podman works and the app's `/healthz` is healthy
  (`C-BOOT-003`). No network is needed for any of it.
- Logins: the serial header (GPIO 14/15, 115200 8N1) or HDMI + USB
  keyboard as `root`, with no password, because reaching either console
  already takes physical access. SSH is off until turned on
  (`C-ACCESS-001`): in Epilykos settings, by Raspberry Pi Imager's SSH
  settings, or with an `authorized_keys` file on BOOT. It is for user
  `epilykos` (sudo) with a password and/or public keys, never root; all of
  it is kept on DATA (`/data/ssh`).
- System actions in Epilykos settings (`C-HOST-001`): Restart, Shut down,
  Change Wi-Fi (opens Bluetooth setup for 15 minutes), the dashboard port
  and the SSH controls. The container reaches them only through `epilykos-hostd`, a
  socket that accepts exactly those actions. Shut down halts the Pi; it
  is safe to unplug once the green light stops flashing.

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
run. Bluetooth setup is offered (`C-PROV-002`, `D-PROV-001`):

- while the box has no network address: at once if no Wi-Fi is saved
  yet, otherwise once the network has been gone for 2 minutes;
- for the first 30 minutes after each boot while no Wi-Fi is saved, so a
  box on Ethernet can still be given Wi-Fi;
- for 15 minutes after "Change Wi-Fi" in Epilykos settings.

Passwords are never written to the journal.

On the console, `iwctl station wlan0 show` and `networkctl` show the
Wi-Fi state; `journalctl -u epilykos-wifi-import -u epilykos-improv`
shows what setup did. A `wifi.txt` that was not imported stays on BOOT
and the journal says why; the commonest cause is pulling the card out
before the computer finished writing it, so eject it first. To join a
network by hand instead, `iwctl station wlan0 connect "<network name>"`
asks for the password and remembers the network.

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
                                            DATA-backed state (epilykos-data-layout,
                                            fstab binds), machine ID, zram,
                                            RAUC system.conf, fw_env.config,
                                            network/mDNS, iwd, Podman storage,
                                            Wi-Fi setup (epilykos-wifi-import,
                                            epilykos-improv, python/epilykos_wifi.py)
  imager/icon.png                         icon for the Raspberry Pi Imager OS list
  build.sh                                fetches a pinned Buildroot release
                                           and builds against this tree
```

Units are enabled (or kept off) by
`usr/lib/systemd/system-preset/50-epilykos.preset`, not by `.wants`
symlinks: Buildroot runs `systemctl preset-all` when it assembles the
rootfs, and that decides. systemd 258 has no catch-all "disable *", so a
unit no preset mentions is enabled; anything that must stay off (OpenSSH's
stock `sshd.service`) is disabled there explicitly.

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

## Decisions this implements

All of the contract's decisions were settled in 0.6.0-draft; the ones
this tree implements directly:

- `D-STORAGE-001` — `genimage.cfg`: BOOT 128 MiB, ROOT-A/B 1536 MiB each,
  DATA grown to fill a card of at least 32 GB.
- `D-BOOT-001` — `boot.cmd`, `uboot.fragment` and RAUC's `system.conf`:
  3 attempts per slot, raw redundant environment at 4 MiB.
- `D-RECOVERY-001` — no recovery target in v1: when both slots run out
  of attempts, `boot.cmd` gives both their attempts back; the documented
  fix is reflashing and restoring a dashboard backup.
- `D-RUNTIME-001` — Podman runs rootful; the app inside the container is
  uid 1000.
- `C-BOOT-001` asks for GPT; the Pi 3B needs an MBR (or a hybrid MBR), so
  the image uses plain MBR — a recorded deviation.
