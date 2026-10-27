# OS release trust (design §6, D2)

Boxes on protocol-2 cards install an OS update from Settings only when every part of its trust chain verifies.

- **Offline OS index root.** An Ed25519 key. Its public half is pinned on BOOT (`os-index-root.pem`) when a card is made, and an OS update can never replace it.
- **Trust policy.** Signed by the root, it lists, for one channel:
  - the delegated index keys;
  - the RAUC CAs;
  - the exact RAUC signing leaves (by SPKI SHA-256).

  Its `trust_epoch` only ever increases. The box keeps the current policy in the U-Boot environment, outside DATA, so a revocation survives a rollback to an older OS and a factory reset.
- **Per-package entry.** A small JSON file signed by a delegated index key. It names the board, channel, sequence, trust epoch and the bundle's exact size and SHA-256.
- **Bundle.** A RAUC verity bundle, CMS-signed by an allowed leaf. Its signed manifest repeats the entry in `[meta.epilykos]`.

None of the three private roots ever touches CI or this repository. The roots are the index root and the two RAUC CAs. `.gitignore` refuses `*.key`.

## Layout

Commit only public material:

```
keys/os-release/<channel>/        dev or stable
  os-index-root.pem               offline index root, public
  os-policy.json                  canonical policy bytes (exactly as signed)
  os-policy.sig                   64-byte Ed25519 signature by the root
```

The image build reads this directory when it exists (`os/board/epilykos/raspberrypi3/post-image.sh`). It seeds the policy into both environment copies and puts the root on BOOT. Without it, protocol-2 builds refuse to run. (`tools/os_test_trust.py` makes throwaway trust for the repository's own tests only.)

## Once, offline: create the roots

Do this on an offline computer, and keep every `.key` off this repository.

```bash
# Offline OS index root (Ed25519)
openssl genpkey -algorithm ED25519 -out os-index-root.key
openssl pkey -in os-index-root.key -pubout -out os-index-root.pem

# OS release CA (signs stable and dev bundles) and OS development CA
# (dev only), 25 years each
for ca in release development; do
  openssl req -new -x509 -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes \
    -keyout os-$ca-ca.key -out os-$ca-ca.pem -days 9131 \
    -subj "/CN=EpilykosOS $ca CA" \
    -addext basicConstraints=critical,CA:TRUE -addext keyUsage=critical,keyCertSign,cRLSign
done
```

Back up the three roots offline, in two places. Losing the index root means every box needs a reflash before it trusts a new key.

## Per channel: delegate CI signing

1. **Create a delegated index key** for the channel, for example `stable-index-1`:

   ```bash
   openssl genpkey -algorithm ED25519 -out stable-index-1.key
   openssl pkey -in stable-index-1.key -pubout -outform DER | tail -c 32 | base64   # public_key below
   ```

2. **Create a RAUC signing leaf** (5 years) and issue it from the right CA. Stable leaves come from the release CA; dev leaves come from the development CA.

   ```bash
   openssl req -new -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes \
     -keyout stable-rauc-1.key -out stable-rauc-1.csr -subj "/CN=EpilykosOS stable signing 1"
   printf 'basicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature\n' > leaf.ext
   openssl x509 -req -in stable-rauc-1.csr -CA os-release-ca.pem -CAkey os-release-ca.key \
     -CAcreateserial -out stable-rauc-1.pem -days 1826 -extfile leaf.ext
   openssl x509 -in stable-rauc-1.pem -pubkey -noout | openssl pkey -pubin -outform DER | sha256sum   # leaf SPKI
   ```

3. **Write and sign the policy.** One command, run from a checkout of this repository on the offline computer, with the paths to the files from the steps above. It writes the exact bytes a box accepts, signs them with the offline root, and checks the result the way the image build does. Nothing is typed by hand.

   ```bash
   python3 tools/make_os_policy.py --channel stable \
     --root-key os-index-root.key --root-pub os-index-root.pem \
     --index-key stable-index-1.key --key-id stable-index-1 \
     --ca os-release-ca.pem --leaf stable-rauc-1.pem \
     --output keys/os-release/stable
   ```

   The dev policy lists both CAs: pass `--ca os-release-ca.pem --ca os-development-ca.pem` and the dev index key and leaf. The stable policy lists only the release CA. The script refuses a root key that doesn't match its public key, and a leaf that none of the given CAs issued. The policy must stay under 6 KiB, which the script and the image build check. It writes `os-index-root.pem`, `os-policy.json` and `os-policy.sig`, and refuses to overwrite an existing folder.

4. **Commit** the three files in `keys/os-release/<channel>/` (public files only, never a `.key`).

5. **Store the CI secrets** in a protected GitHub Actions environment for the channel: the delegated index key, the leaf key and the leaf certificate.
   - `os-stable` accepts only `main`; `os-dev` accepts only `dev`.
   - Pull requests never get these secrets, and a channel's signer is never used for the other channel.
   - Ordinary releases then need no per-image approval.

## What CI publishes, and when

Every build, `dev` included, is a protocol-2 card (it takes OS updates) unless you pick `boot_protocol` 1 by hand, so what is tested is what ships. A protocol-2 image is released as a GitHub release only after its own update package has been uploaded and read back, so a box flashed from it can always be updated.

The `os-package` job in `.github/workflows/os-build.yml` signs and publishes a package for a protocol-2 build. It runs only when every one of these holds (`tools/os_release.py decide`):

- the card was built with the committed maintainer trust for its channel;
- stable comes from `main`, and dev from a manual run on `dev`;
- the repository variable `EPILYKOS_OS_PUBLISH` is `true`. Set it only after the spare-card acceptance has passed (design §12).

For each channel, create a protected environment (**Settings → Environments**): `os-stable`, limited to `main`, and `os-dev`, limited to `dev`. It holds:

| Name | Kind | Value |
|---|---|---|
| `OS_INDEX_KEY` | secret | the delegated Ed25519 index private key (PEM) |
| `OS_RAUC_KEY` | secret | the RAUC signing leaf private key (PEM) |
| `OS_INDEX_KEY_ID` | variable | its `key_id` in the policy, e.g. `stable-index-1` |
| `OS_RAUC_CERT` | variable | the leaf certificate (PEM) |

The job then:

1. makes the package from the build's own rootfs;
2. uploads it to the immutable release `os-<channel>-<sequence>` and reads it back unchanged;
3. moves the `os-<channel>` pointer last: `os-index-<channel>.json`/`.sig` plus the current `os-policy.json`/`.sig`, read back unchanged.

A sequence is never reused. `os_sequence: auto` takes one above the published pointer.

## Revoking a key or a leaf

Write a new policy for the channel:
- leave out the revoked index key or leaf SPKI (or CA);
- add its replacement;
- increase `trust_epoch` by one.

Then sign it with the offline root and commit it. The release job publishes it beside the next package. A box admits a newer policy before the package that needs it, keeps it even if that package then fails, and refuses anything signed under an older epoch or by a key the policy no longer lists. That holds even when the box runs an older retained OS.

Limits:
- A box that is offline cannot learn of a revocation until a policy reaches it, online or with an offline package.
- Certificates are checked at the bundle's signing time (`use-bundle-signing-time`), because the Pi may have no clock. Expiry therefore does not stop a stolen leaf key that backdates its signatures; only the policy revokes it.
- The CA, and every certificate in the chain, must already be valid at the signing time.

## Replacing the root itself

The root's public key is on BOOT, which no OS update writes. Replacing the root needs a reflash, or the future Track B mechanism.

## Verifying what CI produced

```bash
python3 tools/verify_os_release.py --root keys/os-release/stable/os-index-root.pem \
  --policy keys/os-release/stable/os-policy.json --policy-sig keys/os-release/stable/os-policy.sig \
  --entry epilykos-os-0.7-raspberrypi3.entry.json --entry-sig epilykos-os-0.7-raspberrypi3.entry.sig \
  --platform platform.json --bundle epilykos-os-0.7-raspberrypi3.raucb
```

Generating the production keys is the maintainer's explicit step. Nothing in this repository or CI creates them.
