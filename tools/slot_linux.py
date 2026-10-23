#!/usr/bin/env python3
"""Build-time Linux slot identity. No boot/update admission is enabled here."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import struct

from bootstrap_export import parse_baseline
from os_bootstrap import canonical, read_bounded, strict_json, validate_platform

BOOT = 'usr/lib/epilykos/boot'
PLATFORM = 'usr/lib/epilykos/platform.json'
DTB = 'bcm2837-rpi-3-b.dtb'


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def child(root, relative):
    root = Path(root).resolve(strict=True)
    path = root / relative
    if not path.resolve().is_relative_to(root):
        raise ValueError('slot path escapes root')
    return path


def regular(path, maximum):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError('missing/non-regular slot input: ' + str(path))
    return read_bounded(path, maximum)


def check_image(raw, release):
    if len(raw) < 64 or raw[56:60] != b'ARM\x64' or b'Linux version ' + release.encode() + b' ' not in raw:
        raise ValueError('Image is not the expected uncompressed AArch64 kernel release')


def check_dtb(raw):
    if len(raw) < 56:
        raise ValueError('truncated DTB')
    magic, total, offset, strings, reserve, version, last, _, strings_size, structure_size = struct.unpack('>10I', raw[:40])
    if (magic != 0xd00dfeed or total != len(raw) or version != 17 or last > 17 or
            offset < 40 or offset % 4 or reserve < 40 or reserve + 16 > total or
            strings < 40 or strings + strings_size > total or offset + structure_size > total):
        raise ValueError('invalid DTB header')
    end, depth, roots, compatible, terminated = offset + structure_size, 0, 0, None, False
    names = raw[strings:strings + strings_size]
    while offset + 4 <= end:
        token, = struct.unpack_from('>I', raw, offset)
        offset += 4
        if token == 1:
            if depth == 0:
                roots += 1
                if roots != 1:
                    raise ValueError('multiple DTB roots')
            nul = raw.find(b'\0', offset, end)
            if nul < 0 or (depth == 0 and raw[offset:nul] != b''):
                raise ValueError('invalid DTB node')
            depth += 1
            offset = (nul + 4) & ~3
        elif token == 2:
            depth -= 1
            if depth < 0:
                raise ValueError('invalid DTB nesting')
        elif token == 3:
            if depth < 1 or offset + 8 > end:
                raise ValueError('invalid DTB property')
            size, name = struct.unpack_from('>II', raw, offset)
            offset += 8
            nul = names.find(b'\0', name)
            if name >= len(names) or nul < 0 or offset + size > end:
                raise ValueError('invalid DTB property bounds')
            if depth == 1 and names[name:nul] == b'compatible':
                if compatible is not None:
                    raise ValueError('duplicate DTB compatible')
                compatible = raw[offset:offset + size].split(b'\0')
            offset = (offset + size + 3) & ~3
        elif token == 4:
            pass
        elif token == 9:
            terminated = depth == 0 and offset == end
            break
        else:
            raise ValueError('invalid DTB token')
    if not terminated or compatible is None or b'raspberrypi,3-model-b' not in compatible or b'brcm,bcm2837' not in compatible:
        raise ValueError('DTB is not a complete Pi3 Model B tree')


def check_module(raw, release):
    if (len(raw) < 64 or raw[:7] != b'\x7fELF\x02\x01\x01' or
            struct.unpack_from('<HH', raw, 16) != (1, 183)):
        raise ValueError('module must be AArch64 ELF64 ET_REL')
    offset, = struct.unpack_from('<Q', raw, 40)
    entry_size, count, strings_index = struct.unpack_from('<HHH', raw, 58)
    if entry_size != 64 or not 1 <= count <= 4096 or strings_index >= count or offset + count * 64 > len(raw):
        raise ValueError('module ELF section bounds')
    sections = [struct.unpack_from('<IIQQQQIIQQ', raw, offset + i * 64) for i in range(count)]

    def section_data(section):
        start, size = section[4:6]
        if start + size > len(raw):
            raise ValueError('module section outside file')
        return raw[start:start + size]

    names = section_data(sections[strings_index])
    info = []
    for section in sections:
        name = section[0]
        nul = names.find(b'\0', name)
        if name >= len(names) or nul < 0:
            raise ValueError('module section name')
        if names[name:nul] == b'.modinfo':
            info.append(section_data(section))
    if len(info) != 1 or not info[0].endswith(b'\0'):
        raise ValueError('module .modinfo missing/ambiguous')
    values = [v[len(b'vermagic='):] for v in info[0].split(b'\0') if v.startswith(b'vermagic=')]
    if len(values) != 1 or not values[0].startswith(release.encode() + b' '):
        raise ValueError('module vermagic does not match slot kernel')


def module_identity(root, release):
    directory = child(root, 'lib/modules')
    if not directory.is_dir() or sorted(p.name for p in directory.iterdir()) != [release]:
        raise ValueError('missing or stale module release directories')
    path = child(root, 'lib/modules/' + release)
    entries, module_count = [], 0
    for item in sorted(path.rglob('*')):
        if item.is_symlink():
            raise ValueError('module tree contains a symlink')
        if item.is_dir():
            continue
        raw = regular(item, 64 * 1024 * 1024)
        relative = item.relative_to(path).as_posix()
        if not re.fullmatch(r'[A-Za-z0-9_./+-]{1,512}', relative) or len(entries) >= 32768:
            raise ValueError('module tree entry budget/name')
        if item.suffix == '.ko':
            check_module(raw, release)
            module_count += 1
        elif item.name.endswith(('.ko.gz', '.ko.xz', '.ko.zst')):
            raise ValueError('compressed modules require an explicit supported profile')
        entries.append({'path': relative, 'sha256': digest(raw)})
    if not module_count:
        raise ValueError('slot has no verifiable loadable modules')
    return {'module_directory': path.resolve().relative_to(Path(root).resolve()).as_posix(),
            'modules_tree_sha256': digest(canonical(entries)), 'module_count': module_count,
            'module_file_count': len(entries)}


def inventory(root, image, dtb, platform):
    validate_platform(platform)
    release = platform['kernel']
    if not re.fullmatch(r'[A-Za-z0-9._+-]{1,128}', release):
        raise ValueError('invalid kernel release')
    base_raw = regular(child(root, 'usr/lib/epilykos/image.env'), 8192)
    base = parse_baseline(base_raw)
    metadata = dict(line.split('=', 1) for line in base_raw.decode().splitlines() if '=' in line and not line.startswith('#'))
    if (base['channel'] != platform['channel'] or base['release'] != platform['app_release'] or
            metadata.get('EPILYKOS_OS_VERSION') != platform['os_version']):
        raise ValueError('platform/app baseline identity mismatch')
    check_image(image, release)
    check_dtb(dtb)
    return {'schema': 1, 'kernel': release, 'image_sha256': digest(image), 'image_bytes': len(image),
            'dtb_sha256': digest(dtb), 'dtb_bytes': len(dtb), 'platform_sha256': digest(canonical(platform)),
            'image_env_sha256': digest(base_raw), **module_identity(root, release)}


def replace(path, raw):
    if path.is_symlink():
        raise ValueError('refusing slot artifact symlink')
    temporary = path.with_name(path.name + '.new')
    with temporary.open('xb') as stream:
        stream.write(raw)
    temporary.chmod(0o644)
    os.replace(temporary, path)


def stage(root, image_path, dtb_path, platform):
    image, dtb = regular(image_path, 64 * 1024 * 1024), regular(dtb_path, 1024 * 1024)
    manifest = inventory(root, image, dtb, platform)
    boot = child(root, BOOT)
    boot.mkdir(parents=True, exist_ok=True)
    for name, raw in [('Image', image), (DTB, dtb), ('manifest.json', canonical(manifest))]:
        replace(child(root, BOOT + '/' + name), raw)
    replace(child(root, PLATFORM), canonical(platform))
    return manifest


def verify_tree(root):
    platform = strict_json(regular(child(root, PLATFORM), 4096), 4096)
    expected = strict_json(regular(child(root, BOOT + '/manifest.json'), 4096), 4096)
    actual = inventory(root, regular(child(root, BOOT + '/Image'), 64 * 1024 * 1024),
                       regular(child(root, BOOT + '/' + DTB), 1024 * 1024), platform)
    if actual != expected:
        raise ValueError('staged slot identity/content changed')
    return actual


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--image', type=Path, required=True)
    parser.add_argument('--dtb', type=Path, required=True)
    parser.add_argument('--platform', type=Path, required=True)
    args = parser.parse_args()
    try:
        result = stage(args.root, args.image, args.dtb, strict_json(read_bounded(args.platform, 4096), 4096))
    except (ValueError, OSError) as exc:
        parser.exit(1, f'slot staging refused: {exc}\n')
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    main()
