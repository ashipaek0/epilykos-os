# OS updates from Settings — design

Status: **proposed**, 2026-10-22. D1–D3 below were decided by the maintainer on
2026-10-22. Everything marked *Proposed* is a design choice made here; merging
this document approves it, as with other plans in this repository (R15).
Nothing in it is implemented yet. The task breakdown is
[plans/pending/os-dashboard-updates.md](../pending/os-dashboard-updates.md);
the locked v1 policies are in [epilykos-os-v1-scope.md](epilykos-os-v1-scope.md).

## 1. Goal

An administrator opens Settings → Updates, sees that a newer EpilykosOS is out,
installs it while the box keeps monitoring, and restarts when ready. The new
system starts from the other ROOT slot. If it does not come up healthy within
the R03 budget, the box goes back to the system it had, by itself. DATA (the
database, settings, Wi-Fi, SSH, backups) is never rewritten by an update.

## 2. Decisions

| ID | Decision | Effect |
|---|---|---|
| D1 | OS updates replace a whole ROOT slot: kernel, DTB, modules, rootfs and the bundled app (R02A). The boot chain on BOOT (bootcode.bin, start.elf, fixup.dat, config.txt, u-boot.bin, boot.scr) is fixed when the card is flashed and changes only by reflashing. The early boot-chain updater and both-slot recovery (R02B, R02C, R17C) continue as a separate track, **Track B**, which does not gate slot updates. | Slot updates (plan tasks 2–8) ship once they pass on hardware. Plan task 10 is Track B. |
| D2 | The maintainer generates and keeps the signing roots offline: the OS release CA, the OS development CA and the OS index key. CI holds only what it needs to sign (§6). | The plan's custody prerequisite is met; generating the real keys is a maintainer step (§6.4). |
| D3 | A spare SD card is available for destructive boot tests. There is no power-control rig or serial cable; power-cut tests are done by hand at the points §12 names. | Hardware evidence for slot updates is achievable; unattended power-cut automation is not assumed. |

## 3. Where things are today

- MBR card: BOOT (FAT, 128 MiB) at 8 MiB, ROOT-A and ROOT-B (ext4, 1536 MiB
  each), DATA (ext4, grown to fill the card). Fixed PARTUUIDs 45504c4b-01…04.
  U-Boot's environment: two redundant 16 KiB copies at 4 MiB.
- `boot.cmd` follows RAUC's U-Boot bootchooser (BOOT_ORDER, BOOT_x_LEFT = 3)
  and passes `root=PARTUUID=… rauc.slot=A|B`, but loads **Image and the DTB
  from BOOT** (`fatload … :1`). A new rootfs would run on the old kernel.
- RAUC 1.15.2 is in the image with D-Bus, `bootloader=uboot`,
  `data-directory=/data/rauc`, boot-attempts 3, and **no keyring**, so every
  install is refused today.
- `epilykos-mark-good` confirms a slot once DATA is mounted, Podman works and
  the app's /healthz answers, waiting up to 1200 s after multi-user.target.
- App updates (`epilykos_update.py`): an Ed25519-signed manifest, a sequence
  floor on DATA, candidate start and health promotion, the shared maintenance
  flock. `update_apply` with `target: "os"` answers "EpilykosOS updates are not
  published yet"; the app already shows `os.latest_version` when one is offered.
- Factory reset (OS #81) rewrites DATA early in boot from a request on BOOT.
- The v0.6 SD image is 827 MB compressed and carries two copies of the rootfs,
  so one compressed rootfs is roughly 350–450 MB (to be measured, §8.3).

## 4. The slot: boot protocol 2

### 4.1 What a slot contains

Each ROOT filesystem carries its own Linux:

```
/usr/lib/epilykos/boot/Image
/usr/lib/epilykos/boot/bcm2837-rpi-3-b.dtb
/usr/lib/epilykos/platform.json
```

Not `/boot`: that is where BOOT (FAT) is mounted at runtime, which would hide
the slot's files from everything but U-Boot. The modules in `/lib/modules`
come from the same kernel build, so the slot is one unit.

`platform.json` (read-only, written by post-build):

```json
{"schema": 1, "board": "raspberrypi3", "arch": "arm64", "slot_layout": "pi3-mbr-v1",
 "boot_protocol": 2, "channel": "stable", "os_version": "0.7", "os_sequence": 12,
 "source": "<git sha>", "kernel": "6.6.31", "host_api": 2, "app_release": "1.0.4"}
```

### 4.2 boot.scr, protocol 2

The slot choice stays exactly as today. What changes is what happens after it:

1. Pick the slot from BOOT_ORDER and BOOT_x_LEFT; decrement its counter and
   `saveenv` **before** loading anything, so a hang later in this boot still
   uses up an attempt.
2. `ext4load` the slot's Image and DTB from its partition (2 or 3). Check each
   load's result.
3. If either load fails, set that slot's LEFT to 0, `saveenv` and `reset`: the
   next boot takes the other slot. Never fall back to a kernel on BOOT, and
   never boot a DTB left in memory from an earlier load.
4. `booti` as today, with the slot's root and `rauc.slot=`.

When both slots have no attempts left, today's script gives both 3 again and
retries. With D1 that remains the behaviour until Track B provides recovery; it
can only happen when both slots fail, which a slot update alone cannot cause
(the slot it came from stays confirmed).

U-Boot needs ext4 read support (CMD_EXT4/FS_EXT4) and the rootfs must be made
with features U-Boot 2025.10 can read. *Proposed:* pin the rootfs mkfs
options in the defconfig and prove the actual generated `rootfs.ext4` loads on
the Pi (plan task 2), rather than assuming defaults.

### 4.3 Cards flashed before protocol 2

Cards flashed with v0.6 or earlier have a protocol-1 `boot.scr` on BOOT, which
loads the kernel from BOOT. Under D1 nothing rewrites BOOT, so such a card
cannot take a protocol-2 slot: it would boot the new rootfs on the old kernel.
*Proposed:* the host offers OS updates only when the running slot's
`platform.json` says `boot_protocol: 2`. A protocol-1 box shows "This box needs
a one-time reinstall from a new SD card image before it can update its system
from Settings", with Backup → reflash → Restore. Writing boot.scr in place would
be a boot-chain write, which D1 leaves to Track B.

## 5. Health, deadline and watchdog (R03)

*Proposed:*

- `epilykos-boot-health` (replacing `epilykos-mark-good`'s internals) starts
  early, as soon as DATA is mounted, and owns one deadline: 1200 s from Linux
  userspace start (systemd's userspace timestamp), not from when the app
  finishes starting. Within it, DATA must be mounted, Podman must work and the
  app's /healthz must answer. Network, Internet and inverter state are not
  checked.
- Healthy: `rauc status mark-good` (RAUC stays the only runtime writer of slot
  state), then the update job is reconciled (§7).
- Deadline missed, or DATA/Podman unusable: reboot. The attempt was already
  spent in boot.scr, so three failed starts move U-Boot to the other slot.
- Kernel or PID 1 hangs: the BCM2835 hardware watchdog, driven by systemd
  (`RuntimeWatchdogSec` ≈ 10 s, `RebootWatchdogSec` for shutdown hangs). The
  hardware's real maximum timeout is measured in plan task 3.
- A slot that is already confirmed is not re-checked against the deadline on
  every boot. A box that boots slowly for an unrelated reason is not punished
  by having its only good slot used up.

## 6. Trust (R05, D2)

### 6.1 Bundles

Native RAUC signatures (CMS), **verity** bundle format. Two certificate
authorities, both kept offline by the maintainer:

| CA | Signs | Trusted by |
|---|---|---|
| OS release CA | stable and dev release bundles | every image |
| OS development CA | test bundles from branches | dev-channel images only |

Each CA issues one signing certificate for CI. The keyring is part of the slot
(`/etc/rauc/keyring.pem`), so trust anchors update with the OS and a rollback
restores the old ones. App keys are separate (the existing app-manifest key).

Certificate time: the Pi has no clock until a time server answers, and boxes
may be offline. *Proposed:* RAUC checks certificates against the bundle's
signing time (`use-bundle-signing-time`, to be confirmed on RAUC 1.15.2 in
plan task 4), with long-lived certificates (CA 25 years, signing 5 years).
Revocation is by rotation: a later OS ships a keyring without the old CA, and
the sequence floor (§6.3) refuses everything older. There is no CRL or
online check.

### 6.2 The release index

A small signed file says which bundle is current, so a box can decide without
downloading 400 MB:

```json
{"schema": 1, "board": "raspberrypi3", "channel": "stable", "sequence": 12,
 "version": "0.7", "boot_protocol": 2, "min_host_api": 2,
 "bundle": {"url": "https://github.com/ashipaek0/epilykos-os/releases/download/v0.7/epilykos-os-v0.7-raspberrypi3.raucb",
            "size": 412345678, "sha256": "…"},
 "notes_url": "https://github.com/ashipaek0/epilykos-os/releases/tag/v0.7"}
```

`os-index-<channel>.json` plus `.sig`, Ed25519 with an **OS index key**
separate from the app key. It is published on a fixed release tag
`os-<channel>`, the same way as `app-<channel>`. Limits: 8 KiB for the
index, 64 KiB for any download that isn't the bundle.

The bundle repeats what matters in its own signed manifest (`[meta.epilykos]`:
board, channel, sequence, boot_protocol, min_host_api). The host compares the
index entry with RAUC's InspectBundle of the downloaded file and installs only
if they agree. Offline imports (§10) need only the bundle.

### 6.3 Ordering and replay

- Sequence: one counter per channel, assigned by the publishing workflow
  (serialised), never reused. Display versions (v0.7) are not used for ordering.
- Floor: the highest sequence that was **confirmed** on this box. *Proposed:*
  kept in the U-Boot environment (`epk_os_floor`), not on DATA, so a factory
  reset or a damaged DATA does not lower it. Written only after confirmation;
  never lowered. A failed candidate does not raise it.
- Offered: only bundles with sequence greater than both the floor and the
  running slot's own `os_sequence`, same board and channel,
  `boot_protocol` 2, and `min_host_api` ≤ what this host provides.

### 6.4 Keys and CI

Maintainer, once, offline (a written procedure ships with plan task 4):
generate both CAs, the two CI signing certificates and the OS index key; put
the CA keys in offline storage with a backup; put the two signing keys and the
index private key in GitHub Actions secrets of a protected environment that only
`main` and `dev` pushes can use (no pull-request access). Public CA
certificates and the index public key are committed (`keys/os-release/`). CI
signs; nobody approves individual images (R15).

## 7. The update on the box

### 7.1 Who does the work

*Proposed:* `epilykos-os-update.service`, a systemd oneshot worker, not hostd
itself. hostd starts it for an approved job and only reads its state, so a
hostd restart neither stops nor duplicates an install. One job at a time.

### 7.2 Steps and states

| State | What happens |
|---|---|
| `checking` | Fetch and verify the index; compare with floor, slot, protocol and host API. |
| `downloading` | Stream the bundle in 1 MiB writes to `/data/epilykos/os-updater/private/` (root 0700), hashing as it goes, resumable with HTTP Range. Free space is checked before and during (§8). |
| `verifying` | Size and SHA-256 against the index; RAUC InspectBundle for signature and manifest; manifest against the index. |
| `installing` | RAUC InstallBundle over D-Bus into the inactive slot, with RAUC's Progress shown. `activate-installed=false` in system.conf, so installing does not change the boot target. |
| `ready` | Installed; "Restart to finish". Nothing changes until the administrator restarts. The bundle file is deleted. |
| `restarting` | On Restart: RAUC marks the new slot active (BOOT_ORDER, LEFT 3), the host reads it back, the job records the target slot, then reboots. |
| `trying` | The new slot is starting (next boot). |
| `confirmed` / `rolled_back` / `failed` / `cancelled` | Final. Shown once in Settings, kept until dismissed. |

Monitoring keeps running through every state up to `restarting`. Cancel is
possible until `installing` starts; after that the job runs to `ready` or
`failed`, because RAUC cannot be safely interrupted.

### 7.3 Interruptions

- **Power or worker lost while downloading:** the partial file stays and the
  job resumes or restarts from its record.
- **Lost while installing:** the target slot is incomplete but never marked
  active, so the box boots the old slot. The job becomes `failed` ("interrupted
  while installing"), and installing again rewrites the slot.
- **After restart:** before the job knows the outcome, boot reconciliation
  reads the booted slot (`rauc.slot=`) and RAUC's slot state:
  - the target slot booted and was confirmed → `confirmed`, floor raised;
  - the other slot booted after the target ran out of attempts → `rolled_back`,
    with the reason taken from the target's journal on DATA where it exists.

### 7.4 Locks and other maintenance

The worker holds the shared maintenance flock for checking through `ready`
(app backup, restore, snapshot and app update wait or refuse, as today). A
durable guard file `/data/epilykos/os-updater/guard.json` (`active` from
`downloading`, `pending` from `restarting` until confirmed or rolled back)
tells the app and hostd's own actions what is going on after a restart.
hostd refuses `factory_reset`, app `update_apply` and port changes while the
guard is active or pending. App automatic installs never start an OS update
(R06).

### 7.5 Factory reset (OS #81)

The factory-reset file on BOOT is a physical-access request and cannot be
refused by hostd. If it arrives during an update:

- Before `restarting`: DATA is erased, which includes the partial bundle and
  the job. The inactive slot may be half-written but was never activated. The
  box comes up on the old slot as a new box.
- During `trying`: DATA is erased early in the candidate's boot. The candidate
  continues its trial on fresh DATA; it is confirmed or falls back exactly as
  for a box set up from new. The floor and slot state, which are not on DATA,
  are unaffected.

`/data/rauc` (RAUC's install records) is lost by a reset. It is not needed to
boot or to update again.

## 8. Space and size (R09)

### 8.1 Admission

Before downloading, and again before installing, with R09's thresholds
(the same ones app maintenance uses): refuse if DATA's free space after the bundle would fall below
max(10 % of DATA, 1 GiB). Warn in Settings when DATA is below
max(20 %, 2 GiB). While downloading, stop with a clear error if free space
crosses the limit (monitoring keeps writing meanwhile); never delete the
database, backups or images to make room.

### 8.2 What uses space

One bundle at a time on DATA, deleted at `ready` or at the final state. The
inactive slot is overwritten in place (1536 MiB, not on DATA). The new slot's
app image is loaded into Podman storage on DATA on its first boot (an extra
image's worth until the old one is pruned).

### 8.3 Sizes to measure (plan task 4/5)

The real bundle size, the rootfs use inside 1536 MiB with the app OCI archive,
download and install time over Pi 3B Wi-Fi, and the RAM used by RAUC and the
worker. Limits are set from those numbers, not guessed.

## 9. The app after an OS update

The new slot carries its own bundled app (its baseline). The box may already
run a newer app installed from Settings. Today's rule stays: epilykos-run
starts the promoted app only if its sequence is at least the baseline's, so the
newer of the two runs (same channel).

Rollback and the database: if the candidate's app migrates the database and
the OS then falls back, the old slot's app meets a migrated database.
*Proposed release rule:* an OS release may bundle only an app whose database
the previous stable release's app can still read. Apps declare it with an image
label (`io.epilykos.db-compat=<version>`), the release workflow refuses a
bundle that breaks the rule, and the host refuses to activate a slot whose
fallback could not read the database. There is no automatic database restore
on rollback (R12, R04).

## 10. Settings and API

### 10.1 What the administrator sees

Settings → Updates gains an **EpilykosOS** card, shown only on EpilykosOS:

- the current system version; "Up to date", or the new version with a size, a
  release-notes link and **Install**;
- Install asks for the dashboard password again (R06: authenticated, manual);
- progress: Downloading 120 / 412 MB · Verifying · Installing 37 % (no
  invented time estimates);
- **Restart to finish**, also password-confirmed: "Monitoring stops for a few
  minutes while the new system starts. If it doesn't start properly, the box
  goes back to this version by itself.";
- after the restart: "Updated to 0.7", or "0.7 didn't start properly; the box
  went back to 0.6 (reason)", shown until dismissed;
- a protocol-1 box: the one-time reinstall message (§4.3), with no Install.

While a job is active the page polls every 2 s (backing off when offline).
Refreshing or closing the page does not affect the job.

### 10.2 App routes (all admin session + CSRF)

| Route | Body | Host action |
|---|---|---|
| `GET /api/updates/status` | — | `os_status` (adds `host.os`, see below) |
| `POST /api/updates/os/check` | — | `os_check` |
| `POST /api/updates/os/install` | `{sequence, sha256, password}` | `os_install` |
| `POST /api/updates/os/cancel` | `{job}` | `os_cancel` |
| `POST /api/updates/os/restart` | `{job, password}` | `os_restart` |
| `POST /api/updates/os/dismiss` | `{job}` | `os_dismiss` |

The password is checked by the app (the same check as sign-in) and never sent
to the host. `sequence` and `sha256` must match what the last check offered,
so a stale page cannot install something else. `host.os`:

```json
{"version": "0.6", "sequence": 11, "boot_protocol": 2, "host_api": 2,
 "supported": true, "unsupported_reason": null,
 "available": {"version": "0.7", "sequence": 12, "size": 412345678, "sha256": "…", "notes_url": "…"},
 "job": {"id": "…", "state": "downloading", "bytes": 125829120, "total": 412345678, "percent": null,
         "message": "", "started_at": 1792650000},
 "last_result": {"id": "…", "outcome": "confirmed", "version": "0.7", "reason": null, "finished_at": 1792651200}}
```

The existing `os.available` / `os.latest_version` fields, which the app's
update notice already reads, stay for compatibility. `update_apply` with
`target: "os"` keeps refusing; OS installs use only the actions above. Docker
installs show no EpilykosOS card.

## 11. Offline install (R06; plan task 9, after online)

Upload in Settings or a USB stick: the `.raucb` alone. The signed bundle
manifest carries the board, channel, sequence and protocol, so the same checks
apply without the index. The same job, states, locks and Restart step are
used. Inserting a USB stick only lists packages; nothing installs without
**Install** and the password.

## 12. Hardware acceptance (D3)

All tests run on the spare card, never the working box:

1. Two builds with different kernels: boot A on one and B on the other;
   record `uname -r`, modules and `/proc/cmdline`.
2. A good update online: install, restart, confirm. DATA, settings and the
   database are unchanged; monitoring was unaffected until the restart.
3. Bad candidates, each falling back after three attempts with no hands on the
   box: a missing DTB, a kernel that panics, an app that never becomes healthy,
   and a hung boot (hardware watchdog).
4. Power pulled by hand during downloading, installing, right after Restart,
   and during `trying`. Each ends on a working slot with the right job
   outcome.
5. A tampered, unsigned, wrong-CA, wrong-board, wrong-channel or older bundle
   is refused before anything is written to the slot.
6. A newer app installed from Settings survives an OS update; a factory reset
   during `trying` behaves as §7.5.
7. Download and install time, peak RAM, and a 72-hour soak on the updated box
   with production-size data (R18).

Results go in `evidence/os-updates/`.

## 13. What this changes elsewhere

- v1 scope: R02B, R02C and R17C relabelled as Track B (D1). Recorded in
  [epilykos-os-v1-scope.md](epilykos-os-v1-scope.md).
- Contracts (with the implementation, not now): C-BOOT-004 for protocol 2 and
  slot-contained Linux; C-UPDATE-001 for the CAs, the keyring in the slot and
  certificate time; C-HOST-001 for the os_* actions; a new contract for the OS
  index and floor.
- Release workflow: a `.raucb` and a signed `os-index-<channel>.json` from the
  same rootfs as the SD image. The `os-<channel>` pointer moves only after every
  asset has uploaded and been checked.

## 14. Not in this design

Track B (boot-chain updates, automatic recovery when both slots fail or before
U-Boot runs, DATA-independent recovery); Pi 4 and Pi 5; custom update servers
(R11); app downgrade overrides (R12); secure or verified boot (R17B).
