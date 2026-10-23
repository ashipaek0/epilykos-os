# Pi 3B bootstrap: host evidence and pending hardware acceptance

**No physical protocol-2 acceptance has been performed. Shipping activation
remains disabled.** Prior protocol-1 Pi tests do not prove these new paths.

The host tests verify root-signed bootstrap policy material, generate both
16 KiB native records and select them through the compiled native guard. The
pinned native integration build generates the real loader defaults and checks
maximum-policy/runtime capacity. It does not execute U-Boot or prove SD-card
power-loss durability. See [the ABI and delivery boundary](../../plans/decisions/pi3-update-bootstrap.md).

## Bootstrap and legacy migration tools

On a Linux build host with Python 3, a C compiler and OpenSSL:

```sh
python3 tools/test_bootstrap_adoption.py
python3 tools/test_os_slot_acceptance.py
make test-native-env

python3 tools/os_bootstrap.py \
  --defaults /path/to/configured-u-boot/u-boot-initial-env \
  --platform /path/to/platform.json \
  --policy /path/to/root-signed-canonical-policy.json \
  --signature /path/to/root-signed-policy.sig \
  --root-public-key /path/to/trusted-offline-root.pub \
  --output /path/to/new-seed-directory
```

Supply public, offline-root-authorized material from the maintainer; these
commands do not create production keys. Platform JSON currently uses the
strict schema documented in task 2 plus an `app_release` string. Sequence
must be positive and `boot_protocol` must be 2. Producing artifacts does not
change the shipping profile or make a protocol-1 card updatable.

To audit a legacy source, verify its SSH host key independently and place it
in a dedicated known-hosts file. Do not blindly trust `ssh-keyscan` output.
Then, with the existing maintenance SSH account and its enabled sudo access:

```sh
python3 tools/bootstrap_export.py \
  --host 192.0.2.10 --user epilykos \
  --identity-file /path/to/ssh-key \
  --known-hosts-file /path/to/verified-known-hosts \
  --allow-legacy-protocol-1 \
  --target-image-env /path/to/new-root/usr/lib/epilykos/image.env \
  --output /path/to/new-source-audit.json
```

This is a live audit/export and target-baseline preflight, **not a completed
adoption operation**. It holds the existing maintenance lock during export;
no application/OS settings or card contents are changed. No receipt import
is accepted, and no database backup can stand in for source updater state.
It refuses unknown/protocol-2 sources until the native trust reader is wired.
The session protocol and real maintenance exclusion are exercised through
local subprocess pipes; SSH host-key transport and a live source export have
not yet been exercised against a Pi in this stage.

## Manual power-cut evidence runner

Use a spare card only after the experimental protocol-2 image, slot/update
coordinator and reviewed fault-setup procedures exist. The original card is
the repair path under D1/D3. There is no serial-cable or switched-power rig
requirement. The runner reads status; it cannot set up faults, update, reboot
or cut power. No fault injection is performed by the commands below.

Copy `tools/hardware/device-config.example.json` outside the checkout and
fill in the existing maintenance connection, verified host-key file, local
image SHA256, OS source commit and card CID. A CID is readable at
`/sys/block/mmcblk0/device/cid`; use the new card's independently recorded
identity, not an assumed match to the old one. Image SHA256 is verified on
the host; the first snapshot binds the expected source/card on the Pi. This
is attributable evidence, not cryptographic measurement of all flashed bytes.

```sh
python3 tools/hardware/os_slot_acceptance.py init \
  --config /path/to/device-config.json --output /path/to/new-acceptance-run

python3 tools/hardware/os_slot_acceptance.py observe \
  --run /path/to/new-acceptance-run --checkpoint install-power-cut \
  --phase before --note 'Describe the prepared test and observed install phase'
```

Only after the corresponding fault procedure is implemented and reviewed,
cut power manually at that checkpoint, reconnect it and collect `--phase
after` with an honest observation. The runner records card/model, source
metadata, boot ID, kernel, command line, ROOT/DATA mounts, env counters and
floors, RAUC/app status and bounded recent service logs. It requires a valid
before observation for each paired fault test. Command failures and SSH
outages remain failures or unreviewed evidence, never a pass. For rollback,
a different source commit after reboot is preserved for review.

| Checkpoint | Evidence needed before acceptance | Current hardware result |
|---|---|---|
| `boot-a`, `boot-b` | A/B command lines, distinct slot kernels and matching modules; same DATA/settings identity | Pending |
| `ready-power-cut` | Ready source remains selected after spontaneous power loss; no implicit activation | Pending |
| `install-power-cut` | Inactive-slot write interrupted; source boots and critical shared state remains valid | Pending |
| `trial-failure-rollback` | Three failed candidate starts, automatic retained-slot boot; unchanged DATA | Pending |
| `policy-copy-loss` | Acknowledged higher policy/floor survives newest-copy loss and retained-slot native counter refresh; revoked external signer still refused | Pending |
| `both-slots-exhausted` | Bounded non-looping diagnostic halt; no counter refill or app/DATA writes | Pending |
| `factory-reset` | Shared OS floor/trust survive, pending job reconciles, baseline selection follows reset rules | Pending |

The current recorder cannot establish every condition by itself. For example,
unreachable SSH does not prove a terminal loader halt; capture HDMI/manual
observations separately. DATA preservation, module execution, exact signer
revocation and attempt history need their task-specific evidence too. Every
saved event is `unreviewed` or `collection-failed`, and the run remains
`hardware_accepted: false`. Human technical review of the collected evidence
is required for initial hardware acceptance; this is not a manual approval
step for every published image. Automated publication gates remain task 11.
