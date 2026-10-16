# EpilykosOS

EpilykosOS turns a Raspberry Pi into an [Epilykos](https://github.com/ashipaek0/epilykos) energy monitor. Flash it to an SD card, power it on, and use the dashboard from any browser on your network.

> **Status: in development.** It boots and runs Epilykos on a Raspberry Pi 3B. It is still being tested, so expect rough edges.

## What you need

- A Raspberry Pi 3B (the Pi 4 and Pi 5 are planned, best effort)
- A microSD card, 32 GB or larger
- Ethernet or Wi-Fi

## Getting started

1. **Download the image.** Released images are on the [Releases](https://github.com/ashipaek0/epilykos-os/releases) page. Test builds are on the [Actions](https://github.com/ashipaek0/epilykos-os/actions/workflows/os-build.yml) page, under *Artifacts*.
2. **Flash it** to the SD card with Raspberry Pi Imager ("Use custom") or balenaEtcher.
3. **Connect it.** Plug in Ethernet, or set up Wi-Fi in one of these ways:
   - Before first boot, open the card's BOOT drive on your computer, copy `wifi-example.txt` to `wifi.txt`, and fill in your network name, password and country.
   - Or, with the Pi powered on and not yet on a network, open [improv-wifi.com](https://www.improv-wifi.com) in Chrome on a phone or computer and pick **Epilykos** to send it your Wi-Fi over Bluetooth.
4. **Open the dashboard** at `http://epilykos.local:3000`. The first boot takes a few minutes. The first person to open it chooses the admin password.

## What it does

- Starts Epilykos on its own, even with no internet.
- Keeps your data on a separate part of the card, safe across updates.
- Protects the system from sudden power loss.
- Lets you restart, shut down, change Wi-Fi and turn on SSH from **Settings → System**.
- Keeps SSH off unless you turn it on.

Planned: automatic, signed updates with rollback for both the system and the app.

## For developers

- How the image is built and laid out: [`os/README.md`](os/README.md)
- Design and requirements: [`contracts/EPILYKOS-OS-CONTRACTS.md`](contracts/EPILYKOS-OS-CONTRACTS.md)
- `dev` is for testing; `main` is for releases.

```bash
pip install pyyaml
make test        # self-tests
make contracts   # check the contracts
```
