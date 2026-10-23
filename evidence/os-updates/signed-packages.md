# Signed OS packages and shared trust: evidence and pending cases

This is plan task 4 (design §6), and it applies to protocol-2 cards only. Protocol-1 cards keep a RAUC with no keyring, which refuses every bundle.

## Implemented

- **One trust module for the build and both slots.** `epilykos_os_trust.py` lives in the rootfs overlay, and `tools/os_bootstrap.py`, `build_os_bundle.py` and `verify_os_release.py` import it.
  - The root-signed policy is verified against the root pinned on BOOT every time it is used. A valid CRC is not taken as authenticity.
  - Delegated index keys, RAUC CAs and the leaf SPKI allowlist come from that policy.
  - Epochs never go backwards, and a policy at the same epoch must be byte-identical.
- **Shared state.** The current policy, its signature and the scalar epoch are kept in the redundant U-Boot environment.
  - Missing, malformed or unverifiable state turns OS installation off. It is never reset to zero.
  - A newer policy is admitted in one batch through the guarded `fw_setenv`, and the guard re-checks monotonicity under its lock.
  - The same policy is written again rather than skipped, so a write cut between the two copies is replicated before it is acknowledged.
  - Trust advancement never moves the OS floor.
- **Effective keyring.** `rauc.service` runs `ExecStartPre=-epilykos-os-keyring`, which writes `/run/rauc/epilykos-keyring.pem` from the verified policy before every RAUC start.
  - That includes the restart after a policy change.
  - If verification fails, the keyring is removed. RAUC then refuses every bundle while `status` and `mark-good` keep working.
  - There is no slot-local fallback.
- **Protocol-2 `system.conf`.** It sets:
  - `bundle-formats=verity`;
  - `activate-installed=false`;
  - `[keyring] path=/run/rauc/epilykos-keyring.pem` and `use-bundle-signing-time=true`.

  The kernel has `DM_VERITY`, `BLK_DEV_DM`, loop and squashfs built in.
- **Packages.** `tools/build_os_bundle.py` turns the build's own `rootfs.ext4` (the same bytes as ROOT-A and ROOT-B) into a verity bundle.
  - Board, channel, sequence and version come from the rootfs's `platform.json`, never from arguments.
  - It signs the canonical entry with a delegated key.
  - Before publishing, it verifies the result exactly as a box would, using a private sibling directory and one rename.
- **What a box checks before RAUC writes a slot** (`check_bundle`):
  1. the exact authorized size and SHA-256, so payload tampering is caught here and again by dm-verity while installing;
  2. the CMS against the policy's CAs;
  3. the **actual signer's** SPKI in the allowlist;
  4. the signed manifest: compatible string, verity format, exactly `[image.rootfs]`, no hooks and no handler, and `[meta.epilykos]` equal to the entry (board, channel, sequence, boot_protocol, min_host_api, trust_epoch).

  RAUC then repeats its own native verification. A `.raucb` alone is never enough.

## Tests

| Test | What runs | Result |
|---|---|---|
| `tools/test_os_trust_policy.py` (17) | policy schema, root, channel, epochs; env state; admission, including refused and unpersisted writes; keyring never stale; revocation while running the retained older OS | pass |
| `tools/test_verify_os_release.py` (9) | delegated key, tampering, canonical bytes, board/channel/protocol/host API, epoch, links, replay/ordering, CLI | pass |
| `tools/test_os_bundle.py` (14), with **real RAUC 1.15.2** | valid package, with `rauc info` under the protocol-2 `system.conf`; tampered payload; unsigned; wrong CA; unlisted leaf of an allowed CA (RAUC alone accepts it, the box refuses it); manifest disagreement on each field; extra image, hooks and handler; plain format; over-capacity and wrong-channel rootfs; signing-time certificate checks; with no verified keyring, `mark-good` still works while bundles are refused | pass |
| `tools/test_native_platform_env.py` (15), with **real guarded fw-tools** | policy admission through the built writers: one batch, generation +1, both copies; a cut between copies is not acknowledged, and re-admission completes replication | pass |

The bundle and native tests run in the `native-env` CI job (`make test-native-env`), which builds RAUC 1.15.2 with bundle creation.

## Findings

- `use-bundle-signing-time` works on RAUC 1.15.2. A bundle signed while its leaf was valid is accepted after the leaf expired. Signed with an already-expired leaf, it is refused. Without the option, the same bundle is refused today.
- **The whole chain, CA included, must be valid at the signing time.** Production CAs are created before any leaf signs, so this holds.
- RAUC accepts any leaf chaining to a keyring CA. The leaf SPKI allowlist is what makes revoking one CI leaf possible without distrusting its CA, and `check_bundle` enforces it.
- `rauc info` does not read a verity bundle's payload. Payload integrity on the box comes from the entry's SHA-256 before installing and from dm-verity during it.

## Limits (by design)

- Certificates are checked at signing time, so expiry does not stop a stolen leaf key that backdates its signatures. Only a policy revocation does.
- An offline box cannot learn of a revocation that has not reached it.
- Replacing the offline root needs a reflash or Track B.

## Pending

- Production keys and policies are the maintainer's step. The custodian procedure is in `keys/os-release/README.md`.
- An on-box install of a real package on the spare card: the installer (task 6) and hardware acceptance (task 11).
