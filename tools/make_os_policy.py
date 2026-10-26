#!/usr/bin/env python3
"""Write and sign a channel's OS trust policy (keys/os-release/README.md, step 3).

Run it on the offline computer that holds the roots. It reads the files made
in the earlier steps, writes the exact canonical policy bytes and signs them
with the offline index root:

  python3 tools/make_os_policy.py --channel stable \\
      --root-key os-index-root.key --root-pub os-index-root.pem \\
      --index-key stable-index-1.key --key-id stable-index-1 \\
      --ca os-release-ca.pem --leaf stable-rauc-1.pem \\
      --output keys/os-release/stable

Output (the directory must not exist): os-index-root.pem, os-policy.json and
os-policy.sig, ready to commit. The dev policy takes both CAs (--ca twice);
the stable policy only the release CA. To revoke or replace a key, run it
again with the new lists and --trust-epoch one higher than the current one.
Private keys are only read, never copied.
"""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True
from os_bootstrap import canonical, verify_policy  # noqa: E402


def openssl(*args, data=None):
    try:
        return subprocess.run(['openssl', *map(str, args)], input=data, check=True, capture_output=True).stdout
    except subprocess.CalledProcessError as exc:
        raise ValueError(f'openssl {args[0]} failed: {exc.stderr.decode(errors="replace").strip()}') from exc


def spki_der(public_pem):
    return openssl('pkey', '-pubin', '-outform', 'DER', data=public_pem)


def build(channel, root_key, root_pub, index_key, key_id, cas, leaves, trust_epoch=1):
    """Return (policy bytes, signature, root public PEM); verified as a box would."""
    root_public = Path(root_pub).read_bytes()
    if openssl('pkey', '-in', root_key, '-pubout') != root_public:
        raise ValueError('the root key does not match the root public key')
    index_der = spki_der(openssl('pkey', '-in', index_key, '-pubout'))
    if len(index_der) != 44:
        raise ValueError('the delegated index key must be Ed25519')
    ca_texts = [Path(path).read_text(encoding='ascii') for path in cas]
    spkis = []
    for leaf in leaves:
        if not any(_issued_by(leaf, ca) for ca in cas):
            raise ValueError(f'{leaf} was not issued by any of the given CAs')
        public = openssl('x509', '-in', leaf, '-pubkey', '-noout')
        spkis.append(hashlib.sha256(spki_der(public)).hexdigest())
    policy = {'schema': 1, 'board': 'raspberrypi3', 'channel': channel, 'trust_epoch': trust_epoch,
              'index_keys': [{'key_id': key_id, 'public_key': base64.b64encode(index_der[-32:]).decode()}],
              'rauc_cas': ca_texts, 'rauc_leaf_spki_sha256': spkis}
    raw = canonical(policy)
    with tempfile.TemporaryDirectory(prefix='epk-policy-') as directory:
        path = Path(directory) / 'policy'
        path.write_bytes(raw)
        signature = openssl('pkeyutl', '-sign', '-rawin', '-inkey', root_key, '-in', path)
    verify_policy(raw, signature, root_public, board='raspberrypi3', channel=channel)
    return raw, signature, root_public


def _issued_by(leaf, ca):
    try:
        openssl('verify', '-CAfile', ca, leaf)
        return True
    except ValueError:
        return False


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--channel', choices=['dev', 'stable'], required=True)
    parser.add_argument('--root-key', type=Path, required=True, help='offline index root private key (os-index-root.key)')
    parser.add_argument('--root-pub', type=Path, required=True, help='its public key (os-index-root.pem)')
    parser.add_argument('--index-key', type=Path, required=True, help='the channel delegated index private key')
    parser.add_argument('--key-id', required=True, help='its key_id, e.g. stable-index-1')
    parser.add_argument('--ca', type=Path, action='append', required=True, help='a RAUC CA certificate (repeat for dev)')
    parser.add_argument('--leaf', type=Path, action='append', required=True, help='a RAUC signing leaf certificate')
    parser.add_argument('--trust-epoch', type=int, default=1)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.output.exists() or args.output.is_symlink():
            raise ValueError('output already exists; refusing replacement')
        if args.trust_epoch < 1:
            raise ValueError('trust epoch starts at 1')
        raw, signature, root_public = build(args.channel, args.root_key, args.root_pub, args.index_key,
                                            args.key_id, args.ca, args.leaf, args.trust_epoch)
    except (ValueError, OSError) as exc:
        parser.exit(1, f'policy refused: {exc}\n')
    args.output.mkdir(parents=True)
    (args.output / 'os-index-root.pem').write_bytes(root_public)
    (args.output / 'os-policy.json').write_bytes(raw)
    (args.output / 'os-policy.sig').write_bytes(signature)
    print(json.dumps({'channel': args.channel, 'trust_epoch': args.trust_epoch, 'bytes': len(raw),
                      'policy_sha256': hashlib.sha256(raw).hexdigest(), 'output': str(args.output)}, sort_keys=True))


if __name__ == '__main__':
    main()
