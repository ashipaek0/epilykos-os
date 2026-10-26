#!/usr/bin/env python3
"""Publishing OS update packages (design §6.2, §13; plan task 11).

  decide          whether this build may publish a package (fail-closed gate)
  next-sequence   the channel's next sequence from its published pointer
  assets          lay out and re-verify exactly what will be uploaded
  compare         uploaded files, downloaded back, equal the expected bytes

Two GitHub releases per package:

  os-<channel>-<sequence>   immutable: the .raucb, its signed entry and
                            signature (the entry's bundle URL points here)
  os-<channel>              the moving pointer: os-index-<channel>.json and
                            .json.sig (that same entry and signature) and
                            the current root-signed os-policy.json/.sig

The pointer moves last, only after the package assets were uploaded and
read back unchanged. A sequence is never reused: publication refuses an
existing package release or a sequence not above the pointer's.
"""
import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] /
                       'os/board/epilykos/raspberrypi3/rootfs-overlay/usr/lib/epilykos/python'))
import epilykos_os_trust as trust  # noqa: E402

CHANNELS = ('dev', 'stable')


def decide(context):
    """Every condition must hold; the names of those that do not are returned."""
    channel, ref, event = context.get('channel'), context.get('ref', ''), context.get('event')
    authorized = (
        channel == 'stable' and (
            event in ('push', 'workflow_dispatch') and ref == 'refs/heads/main' and context.get('source_on_main') == 'true'
            or event == 'release' and bool(re.fullmatch(r'refs/tags/v[0-9]+\.[0-9]+', ref)))
        or channel == 'dev' and event == 'workflow_dispatch' and ref == 'refs/heads/dev')
    conditions = {
        'protocol_2_image': context.get('boot_protocol') == '2',
        'maintainer_trust': context.get('trust_label') == 'maintainer-trust',
        'authorized_source_for_channel': authorized,
        'build_passed': context.get('build') == 'success',
        'rootfs_uploaded': context.get('rootfs_uploaded') == 'true',
        'sequence_assigned': bool(re.fullmatch(r'[1-9][0-9]{0,18}', context.get('sequence', ''))),
        # Set by the maintainer once the spare-card acceptance has passed (D3).
        'publication_enabled': context.get('publication_enabled') == 'true',
        'not_a_retest': context.get('image_from_run', '') == '',
    }
    return {'publish': all(conditions.values()), 'blocking': [k for k, v in conditions.items() if not v]}


def pointer_entry(raw, channel):
    entry = trust.strict_json(raw, trust.ENTRY_MAX)
    if type(entry) is not dict or entry.get('channel') != channel or entry.get('board') != 'raspberrypi3':
        raise ValueError('the published pointer is for another channel or board')
    return entry


def next_sequence(current, channel):
    """1 for a new channel; otherwise one above the pointer, never reused."""
    if current is None:
        return 1
    return trust.integer(pointer_entry(current, channel)['sequence'], 1) + 1


def check_sequence(current, channel, sequence):
    if current is not None and sequence <= pointer_entry(current, channel)['sequence']:
        raise ValueError(f'sequence {sequence} is not above the published {channel} pointer')


def names(package):
    package = Path(package)
    bundles = sorted(package.glob('*.raucb'))
    if len(bundles) != 1:
        raise ValueError('expected exactly one bundle in the package')
    stem = bundles[0].name[:-len('.raucb')]
    return bundles[0], package / f'{stem}.entry.json', package / f'{stem}.entry.sig'


def assets(package, trust_dir, channel, out, existing=()):
    """Copy and re-verify the exact upload set; refuse collisions with
    assets an immutable package release already has."""
    if channel not in CHANNELS:
        raise ValueError('unknown channel')
    bundle, entry_file, sig_file = names(package)
    trust_dir, out = Path(trust_dir), Path(out)
    raw, sig = entry_file.read_bytes(), sig_file.read_bytes()
    policy_raw, policy_sig = (trust_dir / 'os-policy.json').read_bytes(), (trust_dir / 'os-policy.sig').read_bytes()
    policy = trust.verify_policy(policy_raw, policy_sig, (trust_dir / 'os-index-root.pem').read_bytes(),
                                 board='raspberrypi3', channel=channel)
    platform = {'board': 'raspberrypi3', 'channel': channel, 'host_api': trust.HOST_API}
    entry = trust.verify_os_release(raw, sig, platform, policy)
    release = f'os-{channel}-{entry["sequence"]}'
    if not entry['bundle']['url'].endswith('/' + bundle.name) or f'/{release}/' not in entry['bundle']['url']:
        raise ValueError('the entry does not point at its own immutable package release')
    with tempfile.TemporaryDirectory(prefix='epk-keyring-') as directory:
        keyring = trust.write_keyring(policy, Path(directory) / 'keyring.pem')
        trust.check_bundle(bundle, entry, policy, keyring)
    files = {'package': {bundle.name: bundle, entry_file.name: entry_file, sig_file.name: sig_file},
             'pointer': {f'os-index-{channel}.json': entry_file, f'os-index-{channel}.json.sig': sig_file,
                         'os-policy.json': trust_dir / 'os-policy.json', 'os-policy.sig': trust_dir / 'os-policy.sig'}}
    clash = sorted(set(existing) & set(files['package']))
    if clash:
        raise ValueError(f'package release already has {", ".join(clash)}; a sequence is never reused')
    if out.exists() or out.is_symlink():
        raise ValueError('output already exists')
    for kind, group in files.items():
        (out / kind).mkdir(parents=True)
        for name, source in group.items():
            shutil.copyfile(source, out / kind / name)
    return {'tag': release, 'pointer': f'os-{channel}', 'sequence': entry['sequence'],
            'version': entry['version'], 'trust_epoch': entry['trust_epoch'],
            'package': sorted(files['package']), 'pointer_assets': sorted(files['pointer'])}


def compare(expected, downloaded):
    expected, downloaded = Path(expected), Path(downloaded)
    for path in sorted(expected.iterdir()):
        copy = downloaded / path.name
        if not copy.is_file() or copy.read_bytes() != path.read_bytes():
            raise ValueError(f'{path.name} did not read back unchanged')
    return sorted(p.name for p in expected.iterdir())


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='action', required=True)
    d = sub.add_parser('decide')
    for name in ('channel', 'ref', 'event', 'source-on-main', 'boot-protocol', 'trust-label', 'build',
                 'rootfs-uploaded', 'sequence', 'publication-enabled', 'image-from-run'):
        d.add_argument('--' + name, default='')
    d.add_argument('--github-output', type=Path)
    n = sub.add_parser('next-sequence')
    n.add_argument('--channel', choices=CHANNELS, required=True)
    n.add_argument('--current', type=Path, help='the published pointer entry, if any')
    c = sub.add_parser('check-sequence')
    c.add_argument('--channel', choices=CHANNELS, required=True)
    c.add_argument('--current', type=Path)
    c.add_argument('--sequence', type=int, required=True)
    a = sub.add_parser('assets')
    a.add_argument('--package', type=Path, required=True)
    a.add_argument('--trust', type=Path, required=True)
    a.add_argument('--channel', choices=CHANNELS, required=True)
    a.add_argument('--output', type=Path, required=True)
    a.add_argument('--existing', nargs='*', default=[])
    m = sub.add_parser('compare')
    m.add_argument('--expected', type=Path, required=True)
    m.add_argument('--downloaded', type=Path, required=True)
    args = parser.parse_args()

    def current(path):
        return path.read_bytes()[:trust.ENTRY_MAX + 1] if path and path.is_file() and path.stat().st_size else None
    try:
        if args.action == 'decide':
            result = decide({k.replace('-', '_'): v for k, v in vars(args).items() if k not in ('action', 'github_output')})
            if args.github_output:
                with args.github_output.open('a') as stream:
                    stream.write(f'os_publish={str(result["publish"]).lower()}\n')
        elif args.action == 'next-sequence':
            result = {'sequence': next_sequence(current(args.current), args.channel)}
        elif args.action == 'check-sequence':
            check_sequence(current(args.current), args.channel, args.sequence)
            result = {'sequence': args.sequence}
        elif args.action == 'assets':
            result = assets(args.package, args.trust, args.channel, args.output, args.existing)
        else:
            result = {'unchanged': compare(args.expected, args.downloaded)}
    except (ValueError, OSError, KeyError, subprocess.SubprocessError) as exc:
        parser.exit(1, f'OS release refused: {exc}\n')
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    main()
