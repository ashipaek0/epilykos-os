# EpilykosOS

EpilykosOS is a dedicated operating system for running [Epilykos](https://github.com/ashipaek0/epilykos) on a Raspberry Pi. You flash it, power it on and finish setup from a browser. From then on it runs, updates and recovers by itself, like an appliance.

> **Status:** in development. The design is defined in [`contracts/EPILYKOS-OS-CONTRACTS.md`](contracts/EPILYKOS-OS-CONTRACTS.md). Image build work has started (Buildroot + U-Boot + RAUC, [`os/`](os/)) but is untested — no image has been released yet.

## Hardware

| Board | Support |
|---|---|
| Raspberry Pi 3B | Tested |
| Raspberry Pi 4, Raspberry Pi 5 | Best effort, built on proven upstream board support |

## Features

**Setup with no terminal**
- Plug in Ethernet, or give it your Wi-Fi from your phone over Bluetooth, from Raspberry Pi Imager's settings, or with a small text file on the SD card.
- Then open `http://epilykos.local:3000` in a browser.
- The site time zone is set during setup, so daily totals and forecasts follow local time.

**Reliable operation**
- The system partition is read-only. Your data is kept on a separate persistent partition.
- Epilykos is included in the image, so it starts even when there is no network.
- The application runs in a rootless container. Bluetooth (battery BMS and inverter modules) is handled inside it through the host's BlueZ, with only the device access it needs.
- The database is tuned to lose as little data as possible if the power is cut.
- Logs go to the system journal and don't wear out the SD card.

**Safe updates**
- The OS is updated with A/B slots. Updates are signed, and a failed boot rolls back to the previous slot automatically.
- Application updates are separate from OS updates and can be rolled back on their own.
- Every application image is pinned by digest, and stable devices only run images built from `main`.
- Update manifests are checked for authenticity, and an older release can't be replayed.

**Secure by default**
- SSH is off by default. When it is enabled, it accepts keys only, with no password or root login.
- Verified boot is available as an option.

## Releases

| Channel | Branch | Contents |
|---|---|---|
| Stable | `main` | Reviewed releases for devices in the field |
| Dev | `dev` | Integration builds for testing |

Each Epilykos image build opens a pull request that pins the new image digests. Merging it accepts that release for its channel.

**Versions.** Each OS image built from `main` is the next version, `v0.1`, `v0.2` and so on, and gets a GitHub Release with the SD card image attached. For a bigger jump, go to **Releases → Draft a new release**, type a new tag such as `v1.0` on `main` and publish it. That build is 1.0, and the next one is 1.1. A build run by hand on another branch shows the version it leads to, e.g. `0.3-dev+abc1234`. Appliances never order releases by these numbers; they use the manifest sequence (C-UPDATE-004).

## Development

```bash
pip install pyyaml
make contracts   # validate the contracts
make render      # regenerate contracts/EPILYKOS-OS-CONTRACTS.md
make test        # validator self-tests
```
