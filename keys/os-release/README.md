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

The image build reads this directory when it exists (`os/board/epilykos/raspberrypi3/post-image.sh`). It seeds the policy into both environment copies and puts the root on BOOT. Without it, protocol-2 builds use throwaway TEST-ONLY trust (`tools/os_test_trust.py`), and those images are never published.

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

3. **Write the policy** as canonical JSON. Use sorted keys, no spaces and ASCII only; `tools/os_test_trust.py` shows the exact shape. Then sign it with the root:

   ```json
   {"board":"raspberrypi3","channel":"stable","index_keys":[{"key_id":"stable-index-1","public_key":"…"}],
    "rauc_cas":["-----BEGIN CERTIFICATE-----\n…\n-----END CERTIFICATE-----\n"],
    "rauc_leaf_spki_sha256":["…"],"schema":1,"trust_epoch":1}
   ```

   ```bash
   openssl pkeyutl -sign -rawin -inkey os-index-root.key -in os-policy.json -out os-policy.sig
   ```

   The dev policy lists both CAs. The stable policy lists only the release CA. The policy must stay under 6 KiB, which the image build checks.

4. **Commit** `os-index-root.pem`, `os-policy.json` and `os-policy.sig` under `keys/os-release/<channel>/`.

5. **Store the CI secrets** in a protected GitHub Actions environment for the channel: the delegated index key, the leaf key and the leaf certificate.
   - `os-stable` accepts only `main`; `os-dev` accepts only `dev`.
   - Pull requests never get these secrets, and a channel's signer is never used for the other channel.
   - Ordinary releases then need no per-image approval.

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
