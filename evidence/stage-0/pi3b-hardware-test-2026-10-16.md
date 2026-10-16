# Pi 3B hardware test, 2026-10-16

Image: os-build run 38798994109 (`os/pi3-64bit-bootable`, channel dev),
flashed to a microSD card and booted on the maintainer's Raspberry Pi 3
Model B, HDMI console.

## What worked

- Pi firmware → U-Boot → `boot.scr` → Linux 6.6.31 (AArch64) from ROOT-A;
  login prompt on HDMI; root login with no password.
- DATA grew to fill the card on first boot (resize to 6 823 424 blocks).
- Wi-Fi: brcmfmac and its firmware loaded, iwd scanned (2.4 GHz only, as
  the BCM43438 is), joined the network and systemd-networkd got a DHCP
  address; the Epilykos dashboard answered on port 3000.
- Bluetooth: the BCM43438 came up as `hci0`; `epilykos-improv` registered
  the Improv service and advertised while the box had no network, then
  stopped once Wi-Fi was configured (by design).

## What failed

- `wifi.txt` was not imported: the journal reported no `ssid=` line. The
  file content was valid; the likely causes are a UTF-16 save or a card
  removed before the computer finished writing. Fixed by decoding UTF-16
  and reporting empty files (branch `os/wifi-setup-fixes`).
- Kernel audit notices printed over the HDMI login prompt.
- Cutting power without a shutdown corrupted partitions. This led to the
  read-only ROOT and boot-time DATA repair requirements in `C-BOOT-001`
  (0.6.0-draft) and to Restart/Shut down in the dashboard (`C-HOST-001`).
- The application's first-run setup code is only printed to the server
  log, which an appliance user can't read without a console; dropped
  (`C-HOST-001`).

## Not yet shown

`C-BOOT-000`'s serial log of a slot switch and a forced fallback.
