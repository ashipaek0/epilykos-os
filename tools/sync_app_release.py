#!/usr/bin/env python3
"""Apply one application image release to this repository (C-RELEASE-001).

Input: the client_payload the application repository's image workflows send
with the `app-image-published` repository_dispatch event:

    {"ref": "dev"|"main", "commit": "<sha>", "records": [
        {"service": "epilykos"|"epilykos-bms", "image": "...", "digest": "sha256:...",
         "tag": "...", "commit": "<sha>", "ref": "dev"|"main", "version": "x.y.z"}, ...]}

Effects (no network, no git — the workflow does those):
  - release/digest-records/<ref>.json      append the records (JSON lines, deduplicated)
  - manifests/<channel>.yaml               new manifest for the channel
        ref dev  -> manifests/dev.yaml     (channel: dev)
        ref main -> manifests/stable.yaml  (channel: stable)
    sequence = previous sequence of that manifest + 1 (starts at 1).

The payload is untrusted input: every field is validated and anything
unexpected aborts without writing. Prints a one-line summary on success.

Usage: sync_app_release.py --payload '<json>' [--root DIR]
"""
import argparse
import json
import re
import sys
from pathlib import Path

import yaml

SERVICE_MAP = {'epilykos': 'epilykos', 'epilykos-bms': 'bms-bridge'}
CHANNEL_FOR_REF = {'dev': 'dev', 'main': 'stable'}
DIGEST = re.compile(r'^sha256:[0-9a-f]{64}$')
SHA = re.compile(r'^[0-9a-f]{7,40}$')
IMAGE = re.compile(r'^[a-z0-9][a-z0-9._/-]{0,200}$')
VERSION = re.compile(r'^[0-9A-Za-z.+-]{1,40}$')


class PayloadError(ValueError):
    pass


def validate(payload):
    if not isinstance(payload, dict):
        raise PayloadError('payload must be an object')
    ref, commit, records = payload.get('ref'), payload.get('commit'), payload.get('records')
    if ref not in CHANNEL_FOR_REF:
        raise PayloadError(f'ref must be one of {sorted(CHANNEL_FOR_REF)}, got {ref!r}')
    if not isinstance(commit, str) or not SHA.match(commit):
        raise PayloadError('commit must be a hex sha')
    if not isinstance(records, list) or not records:
        raise PayloadError('records must be a non-empty list')
    by_service = {}
    for r in records:
        if not isinstance(r, dict):
            raise PayloadError('each record must be an object')
        svc = r.get('service')
        if svc not in SERVICE_MAP:
            raise PayloadError(f'unknown service {svc!r}')
        if svc in by_service:
            raise PayloadError(f'duplicate record for {svc}')
        if r.get('ref') != ref or r.get('commit') != commit:
            raise PayloadError(f'{svc}: record ref/commit do not match the payload')
        if not isinstance(r.get('digest'), str) or not DIGEST.match(r['digest']):
            raise PayloadError(f'{svc}: digest must be sha256:<64 hex>')
        if not isinstance(r.get('image'), str) or not IMAGE.match(r['image']):
            raise PayloadError(f'{svc}: invalid image name')
        if not isinstance(r.get('version'), str) or not VERSION.match(r['version']):
            raise PayloadError(f'{svc}: invalid version')
        by_service[svc] = {k: r[k] for k in ('service', 'image', 'digest', 'tag', 'commit', 'ref', 'version') if k in r}
    missing = set(SERVICE_MAP) - set(by_service)
    if missing:
        raise PayloadError(f'records missing for: {", ".join(sorted(missing))}')
    return ref, commit, by_service


def append_records(root, ref, by_service):
    path = root / 'release' / 'digest-records' / f'{ref}.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.read_text().splitlines() if path.exists() else []
    seen = {json.loads(line)['digest'] + '|' + json.loads(line)['service'] for line in existing if line.strip()}
    added = []
    for rec in by_service.values():
        key = rec['digest'] + '|' + rec['service']
        if key not in seen:
            existing.append(json.dumps(rec, sort_keys=True))
            added.append(rec['service'])
    path.write_text('\n'.join(existing) + '\n')
    return added


def write_manifest(root, ref, commit, by_service):
    channel = CHANNEL_FOR_REF[ref]
    path = root / 'manifests' / f'{channel}.yaml'
    previous = yaml.safe_load(path.read_text()) if path.exists() else None
    prev_seq = previous.get('sequence', 0) if isinstance(previous, dict) else 0
    if not isinstance(prev_seq, int):
        raise PayloadError(f'{path.name}: existing sequence is not an integer')
    images = {SERVICE_MAP[svc]: {'digest': rec['digest'], 'source': rec['image']}
              for svc, rec in sorted(by_service.items(), key=lambda kv: SERVICE_MAP[kv[0]])}
    if previous and previous.get('images') == images:
        return channel, prev_seq, False  # same digests: nothing to release
    version = by_service['epilykos']['version']
    manifest = {
        'schema': 1,
        'release': version if channel == 'stable' else f'{version}-dev+{commit[:7]}',
        'channel': channel,
        'sequence': prev_seq + 1,
        'images': images,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    header = (f'# Generated by tools/sync_app_release.py from ashipaek0/epilykos {ref}@{commit[:12]}.\n'
              '# Merging the pull request that changes this file is the trust decision for this release.\n')
    path.write_text(header + yaml.safe_dump(manifest, sort_keys=False))
    return channel, manifest['sequence'], True


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--payload', required=True)
    ap.add_argument('--root', default=str(Path(__file__).resolve().parent.parent))
    args = ap.parse_args(argv)
    try:
        payload = json.loads(args.payload)
        ref, commit, by_service = validate(payload)
        root = Path(args.root)
        added = append_records(root, ref, by_service)
        channel, seq, changed = write_manifest(root, ref, commit, by_service)
    except (json.JSONDecodeError, PayloadError) as e:
        print(f'rejected: {e}', file=sys.stderr)
        return 1
    state = f'sequence {seq}' if changed else f'unchanged (sequence {seq})'
    print(f'{channel} manifest {state}; app {ref}@{commit[:7]} v{by_service["epilykos"]["version"]}; '
          f'records added: {", ".join(added) or "none"}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
