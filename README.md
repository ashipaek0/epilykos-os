# EpilykosOS

EpilykosOS is a dedicated operating system for running [Epilykos](https://github.com/ashipaek0/epilykos) on a Raspberry Pi. You flash it, power it on and finish setup from a browser. From then on it runs, updates and recovers by itself, like an appliance.

## Hardware

- A Raspberry Pi 3B (the Pi 4 and Pi 5 are planned, best effort)
- A microSD card, 32 GB or larger
- Ethernet or Wi-Fi

## Getting started

**Setup with no terminal**
- Download the image from the [Releases](https://github.com/ashipaek0/epilykos-os/releases) page and flash it with Raspberry Pi Imager or balenaEtcher.
- To let Raspberry Pi Imager set up your Wi-Fi, add EpilykosOS to its list with `https://github.com/ashipaek0/epilykos-os/releases/latest/download/epilykos-os-imager.json`.
- Plug in Ethernet, or give it your Wi-Fi from your phone over Bluetooth, add wifi credentials via Raspberry Pi Imager's settings, or with a small wifi.txt file in the boot partition of the SD card.
- Then open `http://epilykos.local:3000` in a browser.
- The site time zone is set during setup, so daily totals and forecasts follow local time.
- The dashboard's port can be changed in Settings → Network; port 80 makes the address just `http://epilykos.local`.

## If something goes wrong

**Can't open the dashboard?** From another computer, try to ping a third device on the same network. If that fails too, the router is keeping devices apart: check its "AP isolation" or "client isolation" settings and restart it. Plugging the Pi into Ethernet also works.

**Forgot the dashboard password?** Put a file named `dashboard-password.txt` with the new password on its first line on the SD card's boot drive, then start the Pi. The file is deleted once it is used. With a keyboard and screen, `epilykos-reset-password` does the same.

**Starting over (factory reset)?** Settings → System → Factory reset erases everything the box keeps (Epilykos data and settings, backups, Wi-Fi networks, SSH, Bluetooth pairings, logs) and restarts it as if newly flashed. Download a backup first if you want your data back later. Without the dashboard, put a file named `factory-reset.txt` containing the word `ERASE` on the SD card's boot drive and start the Pi; with a keyboard and screen, `epilykos-factory-reset` does the same.

**Box can't find your Wi-Fi network?** A Raspberry Pi 3B only sees 2.4 GHz networks, and only sees a router on channel 12 or 13 once it knows its country. It takes the country from the time zone chosen in Epilykos setup; for the very first setup, put `country=` in wifi.txt or choose the Wi-Fi country in Raspberry Pi Imager.

**Wi-Fi setup not showing on improv-wifi.com?** Use "Change Wi-Fi" in Settings → Network, and don't pair the Pi in your computer's Bluetooth menu: setup doesn't need it.

## What it does

**Safe updates**
- Application updates are separate from OS updates and can be rolled back on their own.
- Every application image is pinned by digest.

Signed app updates support health checks, rollback and optional automatic installation. Signed OS bundle installation remains planned. Appliance maintenance and pairing details are in [the feature guide](evidence/stage-1/maintenance-and-pairing.md).

## For developers

| Channel | Branch | Contents |
|---|---|---|
| Stable | `main` | Reviewed releases for devices in the field |
| Dev | `dev` | Integration builds for testing |
