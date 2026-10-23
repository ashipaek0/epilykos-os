# OS updates from Settings — design

Status: **proposed**, revised 2026-10-23 to resolve the consistency review at
the maintainer's request. D1–D3 below were decided on 2026-10-22 and remain
unchanged in scope. Everything marked *Proposed* is a design choice made here; merging
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
| D1 | OS updates replace a whole ROOT slot: kernel, DTB, modules, rootfs and the bundled app (R02A). The boot chain on BOOT (bootcode.bin, start.elf, fixup.dat, config.txt, u-boot.bin, boot.scr) is fixed when the card is flashed and changes only by reflashing. The early boot-chain updater and both-slot recovery (R02B, R02C, R17C) continue as a separate track, **Track B**, which does not gate slot updates. | Track A comprises tasks 1–8, 9 and 11, including their own slot/watchdog/data-safety acceptance. Task 10 alone owns Track B. |
| D2 | The maintainer generates and keeps the signing roots offline: the OS release CA, the OS development CA and the OS index authorization root. The offline index root authorizes separate, channel-scoped CI index-signing keys through a signed trust policy. CI receives delegated signing keys, never the offline roots (§6). | Custody ownership is settled; key generation, signed delegation and protected CI provisioning remain maintainer delivery steps. |
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
- The v0.6 SD image is about 827 MB compressed. Two ROOT copies do not imply
  that the compressed bundle will be half that size: cross-image compression
  and different packaging invalidate that estimate. Measure it (§8.3).

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

The RAUC-compatible A/B variables remain, with these rules:

1. Pick the slot from BOOT_ORDER and BOOT_x_LEFT; decrement its counter and
   `saveenv` **before** loading anything, so a hang later in this boot still
   uses up an attempt. A failed `saveenv` must not launch an uncounted attempt.
   Before any env-writing boot path, apply §6.3's critical-state selection;
   ordinary active-copy selection must not overwrite a newer valid policy.
2. `ext4load` the slot's Image and DTB from its partition (2 or 3). Check each
   load's result.
3. If either load fails or `booti` returns, retain the spent attempt and
   reset; three failed starts exhaust the candidate and select the other
   eligible slot. Do not silently skip the agreed three-attempt policy.
   Never fall back to a kernel on BOOT or boot a DTB left in memory.
4. `booti` as today, with the slot's root and `rauc.slot=`.

Both slots can fail because of shared DATA failure, incompatible application
state or an independently damaged previous slot. Track A must not claim
otherwise. If neither is eligible, stop normal boot in an explicit diagnostic
halt without replenishing counters, app/database writes or a watchdog reboot
loop. Stop the watchdog only in this terminal loader state. This is a safe
failure endpoint requiring spare-card/reflash repair, **not** independent
recovery or satisfaction of Track B. Track B later replaces it with recovery.

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
from Settings", with Backup → spare-card reflash → Restore. Writing boot.scr
in place would be a boot-chain write, which D1 leaves to Track B. Export the
same-channel app floor/promoted identity over an authenticated source-host
session before migration. Adopt that state explicitly; the new baseline or
retained signed app must meet the existing app floor. A database-only restore
does not prove updater ordering and must not silently lower it.

Fresh protocol-2 images seed valid redundant environment records with the
bootstrap signed trust policy and an initialized platform-state schema.
After a factory reset these records remain. If both environment copies are
invalid, normal root boot may use the documented diagnostic/default path, but
OS installation is disabled: never treat missing floor/trust as zero or
re-enrol it automatically. Repair is an authenticated bootstrap/adoption
operation on the spare card; no extra raw control-record area is introduced
by Track A. Update the old image checks that currently require blank envs.

## 5. Health, deadline and watchdog (R03)

*Proposed:*

- `epilykos-boot-health` (replacing `epilykos-mark-good`'s internals) starts
  early, independently of DATA/app readiness, and owns one deadline: 1200 s from Linux
  userspace start (systemd's userspace timestamp), not from when the app
  finishes starting. Within it, DATA must be mounted, Podman must work and the
  app's /healthz must answer. Network, Internet and inverter state are not
  checked.
- On **every** healthy boot, including an already confirmed slot, call
  `rauc status mark-good` to replenish that slot's three attempts. A durable
  confirmation from a previous boot is not permission to skip this refresh.
  For a matching pending transaction, delegate to §7's confirmation
  coordinator: persist confirmation intent **before** this mark-good, then
  finalize promotion. Do not mark-good separately before that coordinator.
  Ordinary healthy reboot refresh does not manufacture a new update result.
- Deadline missed, or DATA/Podman unusable: reboot. The attempt was already
  spent in boot.scr, so three failed starts move U-Boot to the other slot.
- Arm/service the BCM2835 watchdog in the fixed protocol-2 loader and prove
  its handoff into the kernel and systemd. PID1 starting the watchdog cannot
  detect a kernel hang before PID1. Use `RuntimeWatchdogSec` about 10 s,
  a bounded shutdown/reset fallback and a health-supervisor service watchdog
  (`WatchdogSec=15s`, heartbeat at most every 5 s) so a stuck supervisor is
  caught even while PID1 still feeds the hardware watchdog. Bound its Podman
  subprocess calls. The hardware timeout is short (about 16 s maximum),
  separate from the 1200 s application deadline; verify actual timings.
- A known-good slot is checked against the same local health rules on a
  normal reboot. Six consecutive healthy reboots must keep its counter
  refreshed and must not switch slots. DATA failure is shared damage, not
  proof of a bad candidate; stop normal writes and use bounded fallback/halt
  without formatting DATA. A DATA-independent repair UI remains Track B.

## 6. Trust (R05, D2)

### 6.1 Bundles

Native RAUC signatures (CMS), **verity** bundle format. Two certificate
authorities, both kept offline by the maintainer:

| CA | Signs | Trusted by |
|---|---|---|
| OS release CA | stable and dev release bundles | every image |
| OS development CA | test bundles from branches | dev-channel images only |

Each CA issues a signing certificate for CI. Native CMS verification remains
mandatory. Both bootstrap slots also implement the same shared authorization
protocol below. App keys remain separate (the existing app-manifest key).

Certificate time: the Pi has no clock until a time server answers, and boxes
may be offline. *Proposed:* RAUC checks certificates against the bundle's
signing time (`use-bundle-signing-time`, confirmed on RAUC 1.15.2 in plan
task 4; the whole chain, CA included, must be valid at that time), with
long-lived certificates (CA 25 years, signing 5 years).
This supports offline clocks but expiry no longer limits a compromised key
that backdates signatures. The shared signed trust policy and mandatory
package authorization below are therefore required, including offline.
Certificate lifetime alone, a newer OS sequence or removal of a slot-local
CA is not a revocation mechanism.

### 6.2 The release index

A small signed file says which bundle is current, so a box can decide without
downloading 400 MB:

```json
{"schema": 1, "board": "raspberrypi3", "channel": "stable", "sequence": 12,
 "trust_epoch": 2, "index_key_id": "stable-index-2",
 "version": "0.7", "boot_protocol": 2, "min_host_api": 2,
 "bundle": {"url": "https://github.com/ashipaek0/epilykos-os/releases/download/v0.7/epilykos-os-v0.7-raspberrypi3.raucb",
            "size": 412345678, "sha256": "…"},
 "notes_url": "https://github.com/ashipaek0/epilykos-os/releases/tag/v0.7"}
```

`os-index-<channel>.json` plus `.sig` is signed by a channel-scoped CI
Ed25519 key delegated by the **offline OS index authorization root**, separate
from the app key. Publish the current pointer at fixed release tag
`os-<channel>` and retain the exact signed per-package entry/signature beside
its immutable bundle. Limit each entry/index to 8 KiB and other metadata
downloads to 64 KiB. No private root key is required by a build.

The bundle repeats what matters in its own signed manifest (`[meta.epilykos]`:
board, channel, sequence, boot_protocol, min_host_api and trust_epoch). The host compares the
index entry with RAUC's InspectBundle of the downloaded file and installs only
if they agree. **All** delivery paths require the bundle, signed authorization
entry and signature; an updated signed trust policy accompanies them when
needed. A `.raucb` alone is refused. This prevents a compromised RAUC signer
from inventing an otherwise unauthorized higher-sequence package.

### 6.3 Shared trust and revocation across rollback

*Proposed:* the offline index root signs a compact policy containing schema 1,
board/channel, monotonically increasing `trust_epoch`, allowed delegated
index keys (IDs/public keys) and allowed RAUC CA certificates. The root's
policy also allowlists RAUC signing-leaf SPKI fingerprints, so revoking one
CI leaf does not require trusting every leaf issued by the same CA. Verify
the actual bundle signer's SPKI as well as its native CMS chain, not merely
the presence of an allowed certificate somewhere in that chain. The root's
public key is pinned in fixed BOOT and checked identically by both bootstrap
slots. An OS slot cannot replace that root through its own bundle. Replacing
the root itself requires reflash or the future Track B mechanism.

Persist the signed policy outside DATA in the existing redundant U-Boot
environment (`epk_trust_policy`, `epk_trust_policy_sig` and scalar
`epk_trust_epoch`, which must equal the verified policy's epoch). Cap the canonical
policy at 6 KiB, signature at 64 bytes; account for base64 expansion and
require at least 2 KiB env headroom after all boot/OS transaction variables.
Validate this budget with the actual 16 KiB env format before shipping.
Serialize policy, floor, health and RAUC environment writers with one common
locking policy; use one checked batch update for related variables. If a
policy exceeds the budget, refuse it; Track A does not allocate a new raw
region or repartition the card.

Native CRC redundancy alone is not a monotonic commit protocol. Add a
bounded non-wrapping `epk_state_generation` for critical state (policy,
floor, pending/confirmed identities); ordinary counter refresh preserves it.
The loader's first trial-started marker is a critical change and advances
generation; subsequent counter-only writes do not. Every writer follows
this distinction, including the factory-reset transaction annotation.
Before the loader's first saveenv, inspect both native CRC-valid records:
select the greater critical generation, provided it does not lower a valid
record's trust epoch or floor; with equal generations require identical
critical fields and use the native active flag only for counter/order
differences. Equal-generation conflicts, non-monotonic fields or undecodable
critical state stop without overwriting either record. This selection must
run in the fixed bootstrap's env import/guard path, not only after Linux
has already repaired a stale copy.

Critical writers re-read selected state under the common lock, increment
generation, write the new state, then force a second checked native-env
write so **both** valid copies contain it. Read/verify both copies before
acknowledging a policy/floor commit, clearing pending intent or admitting an
external install; a no-op optimization must not skip replication. If a cut
leaves different valid generations, preserve/select the newer and reconcile
replication first. If one record is corrupt, preserve the sole valid record
and complete checked replication before install; never copy an older
selected state over a newer valid record. Acknowledged state was already
replicated, so losing only the newest copy cannot lose that acknowledged
epoch/floor. Both records invalid, conflicting or unrepairable keep install
disabled; no default-zero reinitialization. Test this with actual native env
images and a cut at each write, including normal counter saveenv afterwards.
This is power-failure consistency, not tamper-proof anti-rollback against
root access, a restored whole-card image or physical modification (R17B).
Apply this same generation-aware selection/replication rule to the Linux
`fw_printenv`/`fw_setenv` paths used by RAUC and other env writers; a Python
adapter or loader-only change must not leave those native writers bypassing
the invariant. Test actual RAUC mark-good/mark-active through that path.

Every OS check/install/activation verifies the policy's offline-root
signature, then the delegated entry signature and native RAUC signature.
Use the entry's exact current policy epoch; reject lower epochs and unknown
keys even when the package has a higher OS sequence. Same-epoch policies
must be byte-identical. A higher root-signed policy may be admitted before
the OS candidate; persist/read back it first, retaining its epoch even if
the OS candidate fails. Trust advancement and OS confirmation are separate.
Policy updates are possible without a new OS install and need no per-image
manual approval; only offline delegation/revocation changes require the key
custodian.

Before starting RAUC, generate `/run/rauc/epilykos-keyring.pem` from the
currently verified policy; both slots' system.conf uses that path. On policy
change, wait for the current RAUC transaction to end and reload/restart the
service under maintenance exclusion before another install. Never fall back
to an old slot-local keyring if policy verification fails. A revoked signer
thus stays revoked after OS rollback, factory reset or DATA loss. Retained
installed slots remain bootable; external package admission uses the shared
current policy. An offline box cannot discover a revocation not yet delivered
to it, which remains an explicit limitation.

### 6.4 Ordering and replay

- Sequence: one counter per channel, assigned by the publishing workflow
  (serialised), never reused. Display versions (v0.7) are not used for ordering.
- Floor: the highest sequence that was **confirmed** on this box. *Proposed:*
  kept in the U-Boot environment (`epk_os_floor`), not on DATA, so a factory
  reset or a damaged DATA does not lower it. Written only after confirmation;
  never lowered. A failed candidate does not raise it.
- Offered: only bundles with sequence greater than both the floor and the
  running slot's own `os_sequence`, same board and channel,
  `boot_protocol` 2, and `min_host_api` ≤ what this host provides.
- Store confirmation intent/current confirmed identity with the floor through
  the recoverable environment transaction in §7. Mark-good and floor update
  are not assumed atomic. Never clear pending state until promotion commits.
  Missing/corrupt state disables external installation rather than starting
  a new floor at zero. Internal retained-slot fallback does not lower it.

### 6.5 Keys and CI

Maintainer, once, offline (a written procedure ships with plan task 4):
generate both CAs, the OS index authorization root, delegated CI index keys
and CI RAUC signing certificates. Back up the three roots offline; the
offline index root signs the initial channel-scoped policies. Put only the
delegated CI index private keys and RAUC leaf signing keys in protected
GitHub Actions environments. Stable signing accepts reviewed `main` source;
dev signing accepts reviewed `dev` source; no pull-request secret access or
cross-channel signer use. Public certificates, root public key, policies and
delegations are committed (`keys/os-release/`). CI signs/publishes packages
automatically after the existing maintainer merge authorization (R15).
Environment protection uses scoped refs/permissions, not another manual
per-image reviewer gate. Offline root/delegation changes are infrequent
custodian operations, not routine release approval.

## 7. The update on the box

### 7.1 Who does the work

*Proposed:* `epilykos-os-update.service`, a systemd oneshot worker, not hostd
itself. hostd starts it for an approved job and only reads its state, so a
hostd restart neither stops nor duplicates an install. One job at a time.
Admission requires the running slot to be locally healthy and confirmed;
never start another install while an existing trial is open. Store job/request
IDs, signed authorization, source/target identities, current app selection,
RAUC transaction and service identities in an fsync-and-rename journal with
directory fsync. Requests are idempotent by request ID and exact sequence/hash.

### 7.2 Steps and states

| State | What happens |
|---|---|
| `checking` | Fetch/verify authorization and trust policy; compare floor, slot, protocol and host API. A check alone does not reserve maintenance or activate anything. |
| `downloading` | Stream in 1 MiB writes to owned DATA staging; hash and check headroom. Resume only the same signed identity with a valid HTTP 206 Content-Range/validator; hash the complete file again. Restart if Range is ignored, identity changes or a validator is absent. |
| `verifying` | Verify exact size/hash, current shared trust, both signatures, board/channel/protocol and native manifest agreement. |
| `installing` | RAUC InstallBundle over D-Bus into the inactive slot, with RAUC's Progress shown. `activate-installed=false` in system.conf, so installing does not change the boot target. |
| `ready` | Verify/read back the installed target identity and keep it non-bootable. Persist the pending guard before releasing the live flock. Show "Restart to finish"; retain the root-owned bundle until the job ends. |
| `restarting` | Reacquire the flock and revalidate trust, space, target and app compatibility. Persist activation intent in journal and redundant env **before** RAUC marks that exact slot active. Read back BOOT_ORDER/LEFT, persist activation acknowledgement, then reboot. |
| `trying` | The new slot is starting (next boot). |
| `confirmed` / `rolled_back` / `failed` / `cancelled` / `discarded` | Final. Keep a result ID until dismissed; reclaim only owned terminal staging according to retention rules. |

Monitoring keeps running through every state up to `restarting`. Cancel is
possible until `installing` starts; after that the job runs to `ready` or
`failed`, because RAUC cannot be safely interrupted.

Provide password-confirmed discard in `ready`: verify no activation/writer,
leave the target non-bootable, persist `discarded`, then clear its guard.
No new job may overwrite a ready candidate. Generic reboot/poweroff requests
are refused during writing and direct a ready job to its job-bound Restart
or Discard action. A spontaneous power cut in `ready` boots the old slot.

### 7.3 Interruptions

- **Power or worker lost while downloading:** the partial file stays and the
  job resumes or restarts from its record.
- **Worker/hostd lost while installing:** RAUC's D-Bus writer can survive.
  Persist its transaction UUID, unique bus owner and systemd invocation ID
  before calling it. Retain the active guard until that exact writer is
  proven terminal; a free flock, absent worker or idle replacement daemon
  is insufficient. Match completion and verify target contents independently.
  If service ownership changes or completion is lost, classify interrupted,
  prove old service-cgroup writers gone, mark the uncertain target bad via
  RAUC and read it back before releasing exclusion. Failure to prove these
  leaves `reconciling`/repair-required, not a guessed success.
- **Power lost while installing:** neither source nor BOOT was written.
  On restart keep the partial target non-bootable and reconcile before
  admitting conflicting maintenance. An authenticated retry reinstalls it.
- **Power lost around activation:** journal and `epk_os_pending` already bind
  job, source slot, target slot/sequence/digest and phase. The loader records
  `trial_started` for that job with the counter decrement before launch.
  Source boot before a trial was attempted is interrupted activation/ready,
  not `rolled_back`. Never infer candidate execution from BOOT_ORDER alone.
- **Confirmation:** once the exact candidate is healthy, persist a
  confirmation intent, call RAUC mark-good, then batch-commit highest OS
  floor/confirmed identity and promotion phase in the redundant env. Finalize
  app promotion on DATA and result, then clear pending/guard state. Each
  step is idempotent; startup completes a committed-but-unfinalized promotion
  before ordinary app writes. Neither an HTTP reply nor RAUC write completion
  means success. Previous-slot boot after a recorded failed trial reports
  `rolled_back`; missing evidence reports interrupted/unknown safely.

The bounded `epk_os_pending` and confirmed identity are outside DATA, not a
second raw format. Their serialization and batch updates share the env
budget/lock in §6.3. They allow reconciliation after deliberate DATA reset;
corrupt env disables install rather than silently resetting state.
Use U-Boot-readable scalar fields: `epk_os_pending` is the job UUID,
`epk_os_target`/`epk_os_from` are slot names, with bounded target sequence/hash,
phase and `epk_os_trial_started` job UUID. The loader must not need to decode
an opaque JSON journal or overwrite confirmation intent when refreshing
the trial-started marker. The journal carries detailed app/result metadata.

### 7.4 Locks and other maintenance

The worker holds the existing shared maintenance flock during staging and
installation. Every cooperating admission checks a durable schema-1 guard
**under that same flock**: `idle`, `active`, `pending` or `reconciling`, with
the job ID. Its root-owned public directory is
`/data/epilykos/os-updater/public`, read-only bound into the app at
`/run/epilykos-os-updater`; private state remains root 0700. Bind the directory,
not a single atomically replaced file. Missing/malformed guard on a host that
advertises this protocol fails closed for conflicting maintenance.

`active`/`reconciling` block app updates, restore, backup/snapshot/DB
maintenance, another OS job, factory reset, port/app restarts and shutdown.
`pending` starts at **ready**, persists while awaiting restart and through
trial/reconciliation, and blocks changes to app/data compatibility. Backup/
download and snapshots may run pending; Restart reacquires the lock and
refuses while one is running. Monitoring and ordinary settings writes
continue. Commit `pending` before dropping the live lock. Only confirmed,
rolled-back, cancelled, discarded or safely failed jobs clear it. Reconstruct
it from valid env intent before app start after reboot.

Require OCI label `io.epilykos.os-update-guard=guard-v1`, env
`EPILYKOS_OS_UPDATE_GUARD=/run/epilykos-os-updater/guard.json` and the expected
read-only directory mount, in addition to existing `flock-v1`, before enabling
OS installation. App automatic installs never start an OS update (R06).

### 7.5 Factory reset (OS #81)

The factory-reset file on BOOT is a physical-access request and cannot be
refused by hostd. If it arrives during an update:

- Before `restarting`: DATA is erased, which includes the partial bundle and
  the job. The inactive slot may be half-written but was never activated. The
  box comes up on the old slot as a new box.
- During `trying`: before erasing, record the authorized DATA reset against
  the env pending transaction. Keep OS floor, shared trust policy and pending
  source/target identity intact. Rebuild guard/result state from them before
  starting the app. The trial continues on fresh DATA; confirmation/fallback
  remains truthful without the old journal. The requested reset erases
  independent app state too, so fresh-DATA app selection uses the booted
  slot's signed baseline rather than a missing promoted image.

`/data/rauc` (RAUC's install records) is lost by a reset. It is not needed to
boot. Recreate it for the service, but never use its absence as proof a previous
installation succeeded. Both bootstrap slots implement this reset/reconcile
protocol. A mere DATA mount failure never authorizes formatting it.

## 8. Space and size (R09)

### 8.1 Admission

Before downloading, installing and activating, with R09's thresholds
(the same ones app maintenance uses): refuse if projected peak DATA free space
would fall below max(10 % of DATA, 1 GiB). Count the complete retained bundle,
future OCI import/expanded layers, required scratch and normal-write growth
allowance; do not assume image deduplication or compression saves space.
Warn in Settings when DATA is below
max(20 %, 2 GiB). While downloading, stop with a clear error if free space
crosses the limit (monitoring keeps writing meanwhile); never delete the
database, backups or images to make room.

### 8.2 What uses space

One bundle at a time on DATA, retained through `ready`/trial and reclaimed
only after the job's terminal state is durable and no writer needs it. The
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
newer of the two runs, comparing sequences **only within the same channel**.
Require the selected app to support both the candidate and retained fallback
host APIs, including `guard-v1`. Do not silently use an older baseline if a
required newer promoted image is missing; recover its verified image or stop
for repair.

A newer bundled app is an OS-job **trial** until the whole candidate OS is
confirmed. Preserve the previously confirmed app identity/floor; do not let
the launcher/updater's baseline preference silently promote the trial. If
the candidate or its app fails, the old slot runs the previously confirmed
app, preserving any independent app update completed before this job. A
never-promoted trial does not raise the app floor. After OS confirmation,
finalize app promotion/floor idempotently (§7.3); subsequent OS rollback
retains that confirmed compatible app. Factory reset's deliberate app-state
erase is the explicit exception described in §7.5.

Rollback and the database: if the candidate's app migrates the database and
the OS then falls back, the old slot's app meets a migrated database.
*Proposed release rule:* an OS release may bundle only an app whose database
the actual confirmed fallback app can still read. An image label
(`io.epilykos.db-compat=<version>`) declares the contract but is not proof:
release tests must migrate a representative database with the trial app,
then start/read/write it with the confirmed fallback app. Test dev releases
against their real retained fallback too, not only previous stable. The host
enforces signed compatibility metadata and refuses an unsupported pair.
There is no automatic database restore or destructive rollback (R12, R04).

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
| `POST /api/updates/os/install` | `{sequence, sha256, request_id, password}` | `os_install` |
| `POST /api/updates/os/cancel` | `{job}` | `os_cancel` |
| `POST /api/updates/os/restart` | `{job, password}` | `os_restart` |
| `POST /api/updates/os/discard` | `{job, password}` | `os_discard` (only before activation) |
| `POST /api/updates/os/dismiss` | `{job}` | `os_dismiss` |

The password is checked by the app (the same check as sign-in) and never sent
to the host. `sequence` and `sha256` must match what the last check offered,
so a stale page cannot install something else. `host.os`:

```json
{"version": "0.6", "sequence": 11, "boot_protocol": 2, "host_api": 2,
 "supported": true, "unsupported_reason": null,
 "available": true, "latest_version": "0.7", "latest_sequence": 12,
 "latest": {"version": "0.7", "sequence": 12, "size": 412345678, "sha256": "…", "notes_url": "…"},
 "job": {"id": "…", "state": "downloading", "bytes": 125829120, "total": 412345678, "percent": null,
         "message": "", "started_at": 1792650000},
 "last_result": {"id": "…", "outcome": "confirmed", "version": "0.7", "reason": null, "finished_at": 1792651200}}
```

The existing `os.available` / `os.latest_version` fields, which the app's
update notice already reads, stay for compatibility. `update_apply` with
`target: "os"` keeps refusing; OS installs use only the actions above. Docker
installs show no EpilykosOS card.

`os_install` rechecks the exact signed identity and accepts a repeated request
ID only for that same job. Restart/discard/cancel must name that job; stale or
invalid-state requests do not mutate another job. Existing app progress fields
retain their meaning; expose OS progress separately and do not run the app
update's 15-minute reconnect loop during staging.

## 11. Offline install (R06; plan task 9, after online)

Upload in Settings or USB: `.raucb` **plus its signed authorization entry and
signature**, with the offline-root-signed trust policy if not already known.
The bundle alone is insufficient. Use the same shared epoch/revocation,
sequence, board, hash, space and native CMS checks as online; no alternate
trust path for offline convenience. A higher root-signed policy is persisted
before installation; a stale one cannot undo revocation.

Stream bounded bytes through a separate host-owned upload socket; never put
a bundle in the 16 KiB JSON control request. Metadata admission returns an
opaque one-use transfer ID/token bound to its declared size/hash; no arbitrary
host paths. The 8 KiB authorization plus signature fits a bounded base64
control request; the larger policy is streamed separately with a 6 KiB cap.
Mount USB read-only `nodev,nosuid,noexec` and copy regular selected files into
owned staging. No automatic archive extraction or media execution. The same
job, states, guard and Restart apply. Insertion only discovers packages;
Install still needs the password.

## 12. Hardware acceptance (D3)

All tests run on the spare card, never the working box:

1. Two builds with different kernels: boot A on one and B on the other;
   record `uname -r`, modules and `/proc/cmdline`.
2. A good update online: install, restart, confirm. DATA, settings and the
   database are unchanged; monitoring was unaffected until the restart.
3. Bad candidates, each falling back after three attempts with no hands on the
   box: a missing DTB, a kernel that panics, an app that never becomes healthy,
   and a hung boot (hardware watchdog).
   Also test six healthy normal reboots on a confirmed slot, a stuck health
   supervisor while PID1 feeds the watchdog, and terminal halt when neither
   slot is eligible. Track A does not claim Track B recovery in that halt.
4. Power pulled by hand during downloading, installing, right after Restart,
   and during `trying`. Each ends on a working slot with the right job
   outcome.
5. A tampered, unsigned, wrong-CA, wrong-board, wrong-channel or older bundle
   is refused before anything is written to the slot.
   Deliver a new root-signed policy revoking an old signer, boot the retained
   older OS slot, and reject an otherwise valid higher-sequence forgery by
   that signer through online and offline paths. Test revoked index keys,
   malformed/missing env state and interrupted trust-policy updates too.
6. A newer app installed from Settings survives an OS update; a factory reset
   during `trying` behaves as §7.5.
7. Download and install time, peak RAM, and a 72-hour soak on the updated box
   with production-size data (R18).
8. Kill hostd/worker while RAUC writes: a competing restore stays blocked.
   Test restart from `ready`, a backup pending at Restart, and power loss
   before/after activation intent, mark-active, mark-good and floor commit.
   Assert correct job/result, counter, trust/floor and app/data identities.

Results go in `evidence/os-updates/`.

## 13. What this changes elsewhere

- v1 scope: R02B, R02C and R17C relabelled as Track B (D1). Recorded in
  [epilykos-os-v1-scope.md](epilykos-os-v1-scope.md).
- Contracts (with the implementation, not now): C-BOOT-002 for protocol 2 and
  slot-contained Linux, C-BOOT-003 for health/counter refresh and watchdog;
  C-UPDATE-001 for native CMS, effective shared keyring and certificate time;
  C-HOST-001 for os_* actions; a new contract for OS authorization/policy/floor.
  C-BOOT-004 remains the separate early boot-chain updater (Track B).
- Release workflow: a `.raucb` and a signed `os-index-<channel>.json` from the
  same rootfs as the SD image, retained per-package authorization/signature
  and current offline-root-signed trust policy. The `os-<channel>` pointer
  moves only after every asset has uploaded and been checked. The first
  online release gate is tasks 1–8 plus task 11's online acceptance; task 9
  adds offline acceptance. Neither gate depends on task 10.

## 14. Not in this design

Track B (boot-chain updates, automatic recovery when both slots fail or before
U-Boot runs, DATA-independent recovery); Pi 4 and Pi 5; custom update servers
(R11); app downgrade overrides (R12); secure or verified boot (R17B).
