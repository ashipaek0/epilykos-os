#!/usr/bin/env python3
"""Build and sign one OS update package (design §6.1–6.2, plan task 4).

From the image build's own ext4 rootfs (the same bytes genimage writes to
ROOT-A and ROOT-B), produce:

  <name>.raucb          RAUC verity bundle, CMS-signed by an allowed leaf,
                        whose signed manifest repeats the authorization in
                        [meta.epilykos]
  <name>.entry.json     the signed per-package authorization entry
  <name>.entry.sig      its Ed25519 signature by a delegated index key

Board, channel, sequence and version come from the rootfs's own
/usr/lib/epilykos/platform.json, never from arguments. Before writing
anything final, the result is verified exactly as a box would: policy
against the pinned root, entry against the policy, bundle signer and
manifest against both (epilykos_os_trust). A package that a box would
refuse is never produced.
"""
import argparse
import hashlib
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

EXT4_MAGIC_OFFSET = 1024 + 0x38


def rootfs_platform(rootfs):
    with Path(rootfs).open('rb') as stream:
        stream.seek(EXT4_MAGIC_OFFSET)
        if stream.read(2) != b'\x53\xef':
            raise ValueError('rootfs is not an ext4 image')
    result = subprocess.run(['debugfs', '-R', 'cat /usr/lib/epilykos/platform.json', str(rootfs)],
                            capture_output=True, timeout=60)
    if result.returncode or not result.stdout:
        raise ValueError('rootfs has no /usr/lib/epilykos/platform.json (not a protocol-2 build)')
    return trust.validate_platform(trust.strict_json(result.stdout, 4096))


def manifest(platform, min_host_api, trust_epoch):
    return '\n'.join([
        '[update]', f'compatible={trust.COMPATIBLE}', f'version={platform["os_version"]}',
        f'build={platform["source"]}', '',
        '[bundle]', 'format=verity', '',
        '[image.rootfs]', 'filename=rootfs.ext4', '',
        '[meta.epilykos]', f'board={platform["board"]}', f'channel={platform["channel"]}',
        f'sequence={platform["os_sequence"]}', 'boot_protocol=2', f'min_host_api={min_host_api}',
        f'trust_epoch={trust_epoch}', ''])


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


def sign_ed25519(key, data):
    with tempfile.TemporaryDirectory(prefix='epk-sign-') as directory:
        path = Path(directory) / 'data'
        path.write_bytes(data)
        return subprocess.run(['openssl', 'pkeyutl', '-sign', '-rawin', '-inkey', str(key), '-in', str(path)],
                              check=True, capture_output=True, timeout=30).stdout


def build(*, rootfs, output, rauc, cert, key, index_key, index_key_id, policy_raw, policy_sig, root_pem,
          url_base, notes_url, min_host_api=trust.HOST_API, intermediate=None):
    rootfs, output = Path(rootfs), Path(output)
    if rootfs.stat().st_size > trust.SLOT_BYTES:
        raise ValueError('rootfs is larger than a 1536 MiB slot')
    platform = rootfs_platform(rootfs)
    policy = trust.verify_policy(policy_raw, policy_sig, root_pem,
                                 board=platform['board'], channel=platform['channel'])
    if not 1 <= min_host_api <= platform['host_api']:
        raise ValueError('min_host_api must be at most the host API this build provides')
    if not url_base.startswith('https://') or not notes_url.startswith('https://'):
        raise ValueError('package links must use HTTPS')
    if not re.fullmatch(r'[0-9A-Za-z][0-9A-Za-z._+-]{0,63}', platform['os_version']):
        raise ValueError('os_version cannot name a release file')
    name = f'epilykos-os-{platform["os_version"]}-{platform["board"]}'
    if output.exists() or output.is_symlink():
        raise ValueError('output already exists; refusing replacement')
    # Everything is made and checked in a private sibling, published by one rename.
    stage = Path(tempfile.mkdtemp(prefix='.epk-package-', dir=output.parent))
    try:
        result = package(stage, name, rootfs, platform, policy, rauc, cert, key, intermediate, index_key,
                         index_key_id, url_base, notes_url, min_host_api)
        stage.rename(output)
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    return result


def package(stage, name, rootfs, platform, policy, rauc, cert, key, intermediate, index_key, index_key_id,
            url_base, notes_url, min_host_api):
    content = stage / '.content'
    content.mkdir()
    # A copy of this rootfs, never a different build: the bundle's image is the card's slot.
    shutil.copyfile(rootfs, content / 'rootfs.ext4')
    rootfs_sha256 = sha256_file(rootfs)
    if sha256_file(content / 'rootfs.ext4') != rootfs_sha256:
        raise ValueError('rootfs changed while it was packaged')
    (content / 'manifest.raucm').write_text(manifest(platform, min_host_api, policy['trust_epoch']))
    bundle = stage / f'{name}.raucb'
    command = [str(rauc), 'bundle', '--cert', str(cert), '--key', str(key)]
    if intermediate:
        command += ['--intermediate', str(intermediate)]
    result = subprocess.run([*command, str(content), str(bundle)], capture_output=True, text=True, timeout=1800)
    shutil.rmtree(content)
    if result.returncode:
        raise ValueError(f'rauc bundle failed: {result.stderr.strip()[-400:]}')
    entry = {'schema': 1, 'board': platform['board'], 'channel': platform['channel'],
             'sequence': platform['os_sequence'], 'trust_epoch': policy['trust_epoch'],
             'index_key_id': index_key_id, 'version': platform['os_version'], 'boot_protocol': 2,
             'min_host_api': min_host_api,
             'bundle': {'url': f'{url_base.rstrip("/")}/{bundle.name}', 'size': bundle.stat().st_size,
                        'sha256': sha256_file(bundle)},
             'notes_url': notes_url}
    raw = trust.canonical(entry)
    signature = sign_ed25519(index_key, raw)
    # Verify the result exactly as the box will, before anyone can publish it.
    trust.verify_os_release(raw, signature, platform, policy)
    keyring = trust.write_keyring(policy, stage / '.keyring.pem')
    checked = trust.check_bundle(bundle, entry, policy, keyring)
    keyring.unlink()
    (stage / f'{name}.entry.json').write_bytes(raw)
    (stage / f'{name}.entry.sig').write_bytes(signature)
    return {'bundle': bundle.name, 'entry_sha256': hashlib.sha256(raw).hexdigest(), 'sequence': entry['sequence'],
            'channel': entry['channel'], 'version': entry['version'], 'trust_epoch': entry['trust_epoch'],
            'size': entry['bundle']['size'], 'sha256': entry['bundle']['sha256'],
            'rootfs_sha256': rootfs_sha256, **checked}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--rootfs', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True, help='new directory for the package files')
    parser.add_argument('--rauc', default='rauc')
    parser.add_argument('--cert', type=Path, required=True, help='RAUC signing leaf certificate')
    parser.add_argument('--key', type=Path, required=True, help='RAUC signing leaf private key')
    parser.add_argument('--intermediate', type=Path)
    parser.add_argument('--index-key', type=Path, required=True, help='delegated Ed25519 index private key')
    parser.add_argument('--index-key-id', required=True)
    parser.add_argument('--trust', type=Path, required=True,
                        help='directory with os-policy.json, os-policy.sig and os-index-root.pem')
    parser.add_argument('--url-base', required=True, help='HTTPS release download directory')
    parser.add_argument('--notes-url', required=True)
    parser.add_argument('--min-host-api', type=int, default=trust.HOST_API)
    args = parser.parse_args()
    try:
        result = build(rootfs=args.rootfs, output=args.output, rauc=args.rauc, cert=args.cert, key=args.key,
                       index_key=args.index_key, index_key_id=args.index_key_id,
                       policy_raw=(args.trust / 'os-policy.json').read_bytes(),
                       policy_sig=(args.trust / 'os-policy.sig').read_bytes(),
                       root_pem=(args.trust / 'os-index-root.pem').read_bytes(),
                       url_base=args.url_base, notes_url=args.notes_url, min_host_api=args.min_host_api,
                       intermediate=args.intermediate)
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        parser.exit(1, f'OS package refused: {exc}\n')
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    main()
