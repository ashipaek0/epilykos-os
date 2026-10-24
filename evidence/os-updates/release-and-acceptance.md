# Publishing OS updates and the spare-card acceptance

This is plan task 11 (design §12–§13).

## Publication (implemented)

- **Gate.** A protocol-2 build publishes an update package only when `tools/os_release.py decide` passes:
  - the card was built with the committed maintainer trust for its channel;
  - stable comes from `main`, and dev from a manual run on `dev`;
  - the build succeeded and its rootfs was kept;
  - a sequence was assigned;
  - the repository variable `EPILYKOS_OS_PUBLISH` is `true`.

  The maintainer sets that variable after this acceptance passes. Protocol-1 images still publish exactly as before.
- **Signing.** The `os-package` job runs in the channel's protected environment (`os-stable`, `os-dev`) and uses only delegated keys. It runs one at a time per channel. It builds RAUC 1.15.2, then makes the package from the **same rootfs** as the card's slots.
- **Publishing.**
  1. It checks that the sequence is still unused.
  2. It verifies the upload set again (`os_release.py assets`).
  3. It uploads the immutable `os-<channel>-<sequence>` release and reads it back, comparing bytes and running `verify_os_release.py` on the downloaded files.
  4. It moves the `os-<channel>` pointer **last** and reads that back too.
- **Sequences.** `os_sequence: auto` takes one above the published pointer.
- **Keys.** The environment secrets are listed in `keys/os-release/README.md`. Generating production keys is the maintainer's step.

Tests: `tools/test_os_release.py` (8) covers the gate per condition and channel, the exact upload set, refusals (tampered, unlisted leaf, unlisted index key, stale epoch, URL elsewhere, collision, wrong channel), sequence reuse, read-back, and the workflow's ordering and protection.

## The spare-card acceptance (to do on hardware)

Never on the working box. The tools are `tools/hardware/os_slot_acceptance.py` (`init`, `observe --checkpoint … --phase before|after`, `report`). It records what it sees and never marks anything passed.

### Builds to make

1. **Card build.** Run os-build with `boot_protocol: 2`, `os_sequence: 1` and the dev channel. Flash the `…-protocol2-test-trust-sdcard` artifact onto the spare card.
2. **Update build.** Run os-build again with `boot_protocol: 2`, `os_sequence: 2`, and `test_trust_from_run` set to run 1's ID. Its `…-protocol2-test-package` artifact is a package that card 1 trusts.
3. **Making the package reachable.** The box needs an HTTPS `os-dev` pointer. For a test, either:
   - publish the test package on a fork; or
   - point `EPILYKOS_OS_FEED` (in `epilykos-hostd`'s environment, through a systemd drop-in) at a small HTTPS server holding `os-index-dev.json`/`.sig` and `os-policy.json`/`.sig` from the test trust, and the bundle at the entry's URL.

### Checkpoints (design §12)

| Checkpoint | What to do |
|---|---|
| `boot-a`, `boot-b` | each slot boots its own kernel: `uname -r`, modules, `/proc/cmdline` |
| `healthy-reboots` | six ordinary reboots; counters refreshed each time |
| `good-update` | Settings › Updates: Install, then Restart to finish; confirmed. DATA, settings and the database unchanged; monitoring ran until the restart |
| `bad-dtb`, `bad-kernel`, `bad-app`, `hung-kernel` | each bad candidate falls back after three attempts with no hands on the box |
| `supervisor-hang`, `data-mount-failure`, `both-slots-exhausted` | reset; reboot without formatting; terminal halt |
| `download-power-cut`, `install-power-cut`, `ready-power-cut`, `restart-power-cut`, `trying-power-cut`, `confirm-power-cut` | pull power by hand at each step; each ends on a working slot with the right job result |
| `trial-failure-rollback`, `worker-killed-rauc-alive`, `pending-backup-at-restart`, `discard` | as named; a competing restore stays blocked |
| `refused-packages` | tampered, unsigned, wrong CA, wrong board/channel, or older: refused before the slot is written |
| `revocation-after-fallback`, `policy-copy-loss`, `interrupted-policy-update` | a revoked signer stays revoked on the retained older slot |
| `app-survives-update`, `factory-reset`, `factory-reset-trying`, `db-fallback-compat` | §7.5 and §9 |
| `measurements`, `soak-72h` | download and install time, peak RAM (`MemoryPeak` is in each snapshot), then 72 hours with production-size data |

`python3 tools/hardware/os_slot_acceptance.py report --run RUN` lists what is still pending. After review, the evidence goes in this folder.

## What a box needs before it can update

Cards flashed before protocol 2 (every card in use today) need **one reflash** with a protocol-2 image. BOOT is never updated by an OS update, so this cannot be done remotely (design §4.3).
