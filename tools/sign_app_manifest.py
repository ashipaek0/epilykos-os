#!/usr/bin/env python3
"""Turn a merged application manifest into the signed file appliances fetch
(C-UPDATE-004, D-UPDATE-001).

    sign_app_manifest.py manifests/dev.yaml out/   # needs APP_MANIFEST_SIGNING_KEY_FILE

Writes out/manifest.json (canonical JSON: sorted keys, no spaces) and
out/manifest.json.sig (raw 64-byte Ed25519 signature), then checks the
signature against keys/app-manifest.pub so a mismatched key fails here,
not on every appliance.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def main():
    src, out = Path(sys.argv[1]), Path(sys.argv[2])
    key = os.environ.get('APP_MANIFEST_SIGNING_KEY_FILE')
    if not key:
        sys.exit('APP_MANIFEST_SIGNING_KEY_FILE is not set')
    m = yaml.safe_load(src.read_text())
    app = (m.get('images') or {}).get('epilykos') or {}
    if m.get('schema') != 1 or m.get('channel') not in ('dev', 'stable') or not isinstance(m.get('sequence'), int) \
            or not str(app.get('digest', '')).startswith('sha256:'):
        sys.exit(f'{src}: not a valid application manifest')
    signed = {'schema': 1, 'release': str(m['release']), 'channel': m['channel'], 'sequence': m['sequence'],
              'images': {'epilykos': {'digest': app['digest'], 'source': app['source']}}}
    out.mkdir(parents=True, exist_ok=True)
    body = out / 'manifest.json'
    sig = out / 'manifest.json.sig'
    body.write_bytes(json.dumps(signed, sort_keys=True, separators=(',', ':')).encode())
    subprocess.run(['openssl', 'pkeyutl', '-sign', '-inkey', key, '-rawin', '-in', str(body), '-out', str(sig)], check=True)
    pub = os.environ.get('APP_MANIFEST_PUBKEY', str(ROOT / 'keys/app-manifest.pub'))
    subprocess.run(['openssl', 'pkeyutl', '-verify', '-pubin', '-inkey', pub, '-rawin',
                    '-in', str(body), '-sigfile', str(sig)], check=True, capture_output=True)
    print(f'{m["channel"]}: signed {m["release"]} (sequence {m["sequence"]})')


if __name__ == '__main__':
    main()
