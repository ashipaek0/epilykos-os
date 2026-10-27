# Every-boot health and watchdog: host evidence and pending hardware cases

Plan task 3, protocol-2 cards only. Protocol-1 cards keep
`epilykos-mark-good` unchanged.

## Implemented

- `epilykos-boot-health` (`epilykos_boot_health.py`) replaces
  `epilykos-mark-good` on protocol-2 cards (protocol2-overlay presets). It
  starts early (`DefaultDependencies=no`, `WantedBy=sysinit.target`), with no
  ordering on DATA, the app, RAUC or the network.
- One deadline: 1200 s from systemd's `UserspaceTimestampMonotonic`. Within
  it DATA mounted, `podman info` working and the epilykos container
  `healthy`. Network, Internet and inverters are never checked.
- Every healthy ordinary boot runs `rauc status mark-good`, a confirmed slot
  included. A healthy boot of a pending job's target (trial marker set for
  that job) goes to the confirmation coordinator (`epilykos-os-confirm`,
  task 7), never to mark-good; without the coordinator the trial is left
  unconfirmed. An unreadable environment never guesses a confirmation.
- Deadline missed, or `data.mount` failed: stop the app, `systemctl reboot`,
  forced after 120 s, sysrq reset after 60 s more. The attempt was spent by
  the loader. DATA is never formatted or repaired.
- Every command is a bounded, polled subprocess; the loop (every 2 s) is the
  only `WATCHDOG=1` source for `WatchdogSec=15s`, `Restart=on-watchdog`.
- Watchdog chain: U-Boot `WDT_BCM2835` with autostart at 15 s; the kernel
  feeds a running watchdog until PID1 opens it, at most
  `CONFIG_WATCHDOG_OPEN_TIMEOUT=120`; systemd `RuntimeWatchdogSec=10s`,
  `RebootWatchdogSec=60s`. boot-slot.cmd's terminal halt stops the watchdog.

## Host coverage (`tools/test_boot_health.py`)

Simulated clock, processes, mounts and environment: ordinary healthy boot;
six consecutive healthy boots each refreshing the slot; slow first-boot
import inside the deadline; stalled app past the deadline (reboot, app
stopped first, no mark-good); hung reboot forced then reset; DATA mount
failure rebooting early without touching DATA; DATA mounting late; Podman
unusable; a stuck check killed while heartbeats continue; pending trial
handed to the coordinator; trial without coordinator left unconfirmed;
source-slot boot with a pending job refreshing normally; target before its
trial marker; unreadable environment; no network/inverter checks. Unit
graph: early, independent of DATA/app/RAUC/network, notify with watchdog;
presets per protocol; watchdog settings below the hardware maximum.

## Pending hardware cases (spare card)

None of these is shown by host tests:

- watchdog actually armed by U-Boot and handed through the kernel to
  systemd (`wdctl`, timing of a deliberate hang before PID1);
- six healthy reboots keeping the same slot with counters refreshed;
- kernel panic, missing DTB, app never healthy and a hung boot each falling
  back after three attempts with no hands on the box;
- a stuck supervisor (SIGSTOP) reset while PID1 still feeds the watchdog;
- both slots ineligible ending in the loader's halt without a reset loop.
