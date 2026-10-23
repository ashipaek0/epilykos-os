#!/usr/bin/env python3
"""Fail-closed publication/diagnostic decisions for the OS image workflow.

GitHub job results and artifact upload outcome are inputs, never QEMU results.
The workflow consumes the emitted outputs; stdout retains the decision as JSON.
"""
import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path

SHA256 = re.compile(r'[0-9a-f]{64}')


def decide(context):
    validated = context.get('validation') == 'success'
    artifact = context.get('artifact') == 'true'
    previous = context.get('image_from_run', '')
    retest = bool(re.fullmatch(r'[1-9][0-9]{0,19}', previous))
    event = context.get('event')
    ref = context.get('ref', '')
    source = context.get('source_on_main') == 'true'
    protocol_1 = context.get('boot_protocol', '') in ('', '1')
    authorized_event = (
        event in ('push', 'workflow_dispatch') and ref == 'refs/heads/main'
        or event == 'release' and bool(re.fullmatch(r'refs/tags/v[0-9]+\.[0-9]+', ref))
    )
    conditions = {
        'validation_passed': validated,
        'build_passed': context.get('build') == 'success',
        'image_uploaded': artifact,
        'image_hash_recorded': bool(SHA256.fullmatch(context.get('image_sha256', ''))),
        'stable_channel': context.get('channel') == 'stable',
        'source_on_main': source,
        'authorized_event': authorized_event,
        'new_or_explicit_release': context.get('new_version') == 'true' or event == 'release',
        'not_a_retest': previous == '',
        # Protocol-2 cards (OS updates) are development builds until their
        # hardware acceptance; they carry test or unaccepted trust.
        'protocol_1_image': protocol_1,
    }
    return {
        'publish': all(conditions.values()),
        # The QEMU diagnostic boots the kernel from BOOT, which a protocol-2
        # card no longer carries.
        'diagnose': validated and (retest or artifact) and protocol_1,
        'blocking': [name for name, passed in conditions.items() if not passed],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verify-artifact', type=Path)
    parser.add_argument('--sha256')
    args = parser.parse_args()
    if args.verify_artifact is not None:
        if not SHA256.fullmatch(args.sha256 or ''):
            parser.error('a valid build-time --sha256 is required')
        try:
            if args.verify_artifact.stat().st_size == 0:
                raise ValueError('image artifact is empty')
            with args.verify_artifact.open('rb') as image:
                actual = hashlib.file_digest(image, 'sha256').hexdigest()
            if actual != args.sha256:
                raise ValueError('image artifact differs from the successful build output')
        except (OSError, ValueError) as error:
            print(str(error), file=sys.stderr)
            return 1
        print(json.dumps({'artifact_verified': True, 'sha256': actual}))
        return 0
    if args.sha256:
        parser.error('--sha256 requires --verify-artifact')
    keys = ('validation', 'build', 'artifact', 'channel', 'event', 'ref',
            'new_version', 'source_on_main', 'image_from_run', 'image_sha256', 'boot_protocol')
    result = decide({key: os.environ.get('POLICY_' + key.upper(), '') for key in keys})
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'], 'a') as output:
            for key in ('publish', 'diagnose'):
                output.write(f'{key}={str(result[key]).lower()}\n')
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    sys.exit(main())
