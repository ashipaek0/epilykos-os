#!/usr/bin/env python3
"""Inspect complete Linux artifacts from generated ext4 bytes, read-only.

Structural check only. Does not establish that U-Boot reads/boots the image.
"""
import argparse
import json
from pathlib import Path
import re
import subprocess
import tempfile

from os_bootstrap import strict_json
from slot_linux import BOOT, PLATFORM, verify_tree


def debugfs(image, command):
    result = subprocess.run(['debugfs', '-R', command, str(image)], capture_output=True, timeout=60)
    if result.returncode:
        raise ValueError('debugfs failed to read generated slot')
    return result.stdout


def dump(image, source, destination):
    if not re.fullmatch(r'[A-Za-z0-9_./+-]+', str(destination)):
        raise ValueError('debugfs temporary path must not contain whitespace/metacharacters')
    destination.parent.mkdir(parents=True, exist_ok=True)
    debugfs(image, f'dump {source} {destination}')
    if not destination.is_file():
        raise ValueError('generated slot missing ' + source)


def directory_inode(image, path):
    match = re.search(rb'^Inode:\s+(\d+)\s+Type:\s+directory\b', debugfs(image, 'stat ' + path))
    if not match:
        raise ValueError('generated slot missing runtime directory: ' + path)
    return int(match[1])


def check_rootfs(image):
    image = Path(image).resolve(strict=True)
    if not image.is_file():
        raise ValueError('rootfs input must be a regular image, not a device')
    result = subprocess.run(['e2fsck', '-fn', str(image)], capture_output=True, timeout=120)
    if result.returncode:
        raise ValueError('generated ext4 is not clean/readable')
    with tempfile.TemporaryDirectory(prefix='epk-slot-') as directory:
        root = Path(directory)
        for relative in [PLATFORM, BOOT + '/manifest.json', BOOT + '/Image', BOOT + '/bcm2837-rpi-3-b.dtb',
                         'usr/lib/epilykos/image.env']:
            dump(image, '/' + relative, root / relative)
        manifest = strict_json((root / BOOT / 'manifest.json').read_bytes(), 4096)
        module_directory = manifest.get('module_directory', '')
        if not re.fullmatch(r'(usr/)?lib/modules/[A-Za-z0-9._+-]{1,128}', module_directory):
            raise ValueError('invalid immutable module directory')
        module_parent = Path(module_directory).parent
        # Resolve /lib/modules in the actual ext4, before constructing the
        # equivalent merged-/usr alias in the temporary extraction tree.
        if directory_inode(image, '/lib/modules') != directory_inode(image, '/' + str(module_parent)):
            raise ValueError('runtime module path differs from immutable inventory')
        destination = root / module_parent.parent
        destination.mkdir(parents=True, exist_ok=True)
        debugfs(image, f'rdump /{module_parent} {destination}')
        if module_directory.startswith('usr/'):
            (root / 'lib').symlink_to('usr/lib')
        return verify_tree(root)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rootfs', type=Path, required=True)
    args = parser.parse_args()
    try:
        result = check_rootfs(args.rootfs)
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        parser.exit(1, f'slot image refused: {exc}\n')
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    main()
