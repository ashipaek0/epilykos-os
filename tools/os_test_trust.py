#!/usr/bin/env python3
"""Generate TEST-ONLY OS trust material for protocol-2 test cards.

Production trust is the maintainer's offline roots (design §6.5, D2); this
makes a throwaway equivalent for building and accepting test cards on a
spare SD card, never for a published image:

  os-index-root.pem   offline index root public key (pinned on BOOT)
  os-policy.json      root-signed trust policy, epoch 1 (canonical bytes)
  os-policy.sig       its 64-byte Ed25519 signature
  private/            TEST-ONLY private keys: index root, delegated index
                      key, RAUC CA and signing leaf (to sign test packages)

A card built from these trusts only packages signed by these test keys.
The output directory must not exist; nothing is overwritten.
"""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import subprocess
import sys

sys.dont_write_bytecode = True
from os_bootstrap import canonical, verify_policy  # noqa: E402

NOTICE = """TEST-ONLY OS trust material ({channel}).

Made by tools/os_test_trust.py for building and accepting protocol-2 test
cards on a spare SD card. These keys are not the maintainer's offline roots:
never publish an image, policy or package made with them, and never flash a
card built with them onto the working box.
"""


def openssl(*args, data=None):
    return subprocess.run(['openssl', *map(str, args)], input=data, check=True, capture_output=True).stdout


def ed25519(path):
    openssl('genpkey', '-algorithm', 'ED25519', '-out', path)
    path.chmod(0o600)
    return openssl('pkey', '-in', path, '-pubout')


def generate(output, channel, key_id):
    output = Path(output)
    if output.exists() or output.is_symlink():
        raise ValueError('output already exists; refusing replacement')
    private = output / 'private'
    private.mkdir(parents=True, mode=0o700)
    (output / 'TEST-ONLY.txt').write_text(NOTICE.format(channel=channel))

    root_public = ed25519(private / 'os-index-root.key')
    (output / 'os-index-root.pem').write_bytes(root_public)
    index_public = ed25519(private / f'{key_id}.key')
    index_der = openssl('pkey', '-pubin', '-outform', 'DER', data=index_public)

    # RAUC: a CA (25 years) and one signing leaf (5 years), as design §6.1.
    ca_key, ca_pem = private / 'rauc-ca.key', output / 'rauc-ca.pem'
    openssl('req', '-new', '-x509', '-newkey', 'ec', '-pkeyopt', 'ec_paramgen_curve:P-256', '-nodes',
            '-keyout', ca_key, '-out', ca_pem, '-days', 9131, '-subj', f'/CN=EpilykosOS TEST-ONLY {channel} RAUC CA',
            '-addext', 'basicConstraints=critical,CA:TRUE', '-addext', 'keyUsage=critical,keyCertSign,cRLSign')
    leaf_key, leaf_csr, leaf_pem = private / 'rauc-signing.key', private / 'rauc-signing.csr', output / 'rauc-signing.pem'
    openssl('req', '-new', '-newkey', 'ec', '-pkeyopt', 'ec_paramgen_curve:P-256', '-nodes',
            '-keyout', leaf_key, '-out', leaf_csr, '-subj', f'/CN=EpilykosOS TEST-ONLY {channel} RAUC signing')
    extensions = private / 'leaf.ext'
    extensions.write_text('basicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature\n')
    openssl('x509', '-req', '-in', leaf_csr, '-CA', ca_pem, '-CAkey', ca_key,
            '-CAserial', private / 'rauc-ca.srl', '-CAcreateserial',
            '-out', leaf_pem, '-days', 1826, '-extfile', extensions)
    for path in (ca_key, leaf_key):
        path.chmod(0o600)
    leaf_spki = openssl('pkey', '-pubin', '-outform', 'DER',
                        data=openssl('x509', '-in', leaf_pem, '-pubkey', '-noout'))

    policy = {'schema': 1, 'board': 'raspberrypi3', 'channel': channel, 'trust_epoch': 1,
              'index_keys': [{'key_id': key_id, 'public_key': base64.b64encode(index_der[-32:]).decode()}],
              'rauc_cas': [ca_pem.read_text(encoding='ascii')],
              'rauc_leaf_spki_sha256': [hashlib.sha256(leaf_spki).hexdigest()]}
    raw = canonical(policy)
    (output / 'os-policy.json').write_bytes(raw)
    policy_file = private / 'policy.bin'
    policy_file.write_bytes(raw)
    signature = openssl('pkeyutl', '-sign', '-rawin', '-inkey', private / 'os-index-root.key', '-in', policy_file)
    policy_file.unlink()
    (output / 'os-policy.sig').write_bytes(signature)
    # The same check the image build and the box make.
    verify_policy(raw, signature, root_public, board='raspberrypi3', channel=channel)
    return {'channel': channel, 'trust_epoch': 1, 'index_key_id': key_id,
            'policy_sha256': hashlib.sha256(raw).hexdigest(),
            'rauc_leaf_spki_sha256': policy['rauc_leaf_spki_sha256'][0]}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--channel', choices=['dev', 'stable'], default='dev')
    args = parser.parse_args()
    try:
        result = generate(args.output, args.channel, f'{args.channel}-test-1')
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f'test trust refused: {exc}\n')
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    main()
