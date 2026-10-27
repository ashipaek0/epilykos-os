#!/usr/bin/env python3
"""Verify a published OS package the way a box does (design §6, plan task 4).

Inputs are the pinned offline root, the root-signed trust policy, the
signed per-package entry and, optionally, the downloaded bundle and the
platform it would be installed on. Checks, in order: policy against the
root (and monotonic to --current-policy when given); entry against the
policy's delegated keys and this platform; ordering against --floor; then
the bundle's exact bytes, actual CMS signer SPKI and signed manifest.

A `.raucb` alone is never enough: the entry and its signature are required.
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] /
                       'os/board/epilykos/raspberrypi3/rootfs-overlay/usr/lib/epilykos/python'))
import epilykos_os_trust as trust  # noqa: E402


def verify(*, root_pem, policy_raw, policy_sig, entry_raw, entry_sig, platform, bundle=None, floor=None,
           current=None):
    policy = trust.verify_policy(policy_raw, policy_sig, root_pem, board=platform['board'],
                                 channel=platform['channel'], current=current)
    entry = trust.verify_os_release(entry_raw, entry_sig, platform, policy)
    if floor is not None:
        trust.admissible(entry, platform, floor)
    result = {'sequence': entry['sequence'], 'version': entry['version'], 'trust_epoch': entry['trust_epoch'],
              'index_key_id': entry['index_key_id']}
    if bundle is not None:
        with tempfile.TemporaryDirectory(prefix='epk-verify-') as directory:
            keyring = trust.write_keyring(policy, Path(directory) / 'keyring.pem')
            result.update(trust.check_bundle(bundle, entry, policy, keyring))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--root', type=Path, required=True, help='pinned os-index-root.pem')
    parser.add_argument('--policy', type=Path, required=True)
    parser.add_argument('--policy-sig', type=Path, required=True)
    parser.add_argument('--current-policy', type=Path, help='previously verified policy (for monotonicity)')
    parser.add_argument('--entry', type=Path, required=True)
    parser.add_argument('--entry-sig', type=Path, required=True)
    parser.add_argument('--platform', type=Path, required=True, help='the target platform.json')
    parser.add_argument('--bundle', type=Path)
    parser.add_argument('--floor', type=int)
    args = parser.parse_args()
    try:
        current = None
        if args.current_policy:
            raw = args.current_policy.read_bytes()
            current = {'raw': raw, 'trust_epoch': trust.strict_json(raw, trust.POLICY_MAX)['trust_epoch']}
        result = verify(root_pem=args.root.read_bytes(), policy_raw=args.policy.read_bytes(),
                        policy_sig=args.policy_sig.read_bytes(),
                        entry_raw=args.entry.read_bytes()[:trust.ENTRY_MAX + 1],
                        entry_sig=args.entry_sig.read_bytes()[:65],
                        platform=trust.load_platform(args.platform), bundle=args.bundle, floor=args.floor,
                        current=current)
    except (ValueError, OSError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        parser.exit(1, f'OS release refused: {exc}\n')
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    main()
