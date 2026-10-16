# EpilykosOS

EpilykosOS turns a Raspberry Pi into an [Epilykos](https://github.com/ashipaek0/epilykos) energy monitor. Flash it to an SD card, power it on, and use the dashboard from any browser on your network.

## Hardware

- A Raspberry Pi 3B (the Pi 4 and Pi 5 are planned, best effort)
- A microSD card, 32 GB or larger
- Ethernet or Wi-Fi

## Getting started

**Setup with no terminal**
- Plug in Ethernet, or give it your Wi-Fi from your phone over Bluetooth, add wifi credentials via Raspberry Pi Imager's settings, or with a small wifi.txt file in the boot partition of the SD card.
- Then open `http://epilykos.local:3000` in a browser.
- The site time zone is set during setup, so daily totals and forecasts follow local time.

## What it does

**Safe updates**
- Application updates are separate from OS updates and can be rolled back on their own.
- Every application image is pinned by digest.

Planned: automatic, signed updates with rollback for both the system and the app.

## For developers

| Channel | Branch | Contents |
|---|---|---|
| Stable | `main` | Reviewed releases for devices in the field |
| Dev | `dev` | Integration builds for testing |
