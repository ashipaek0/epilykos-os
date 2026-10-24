#!/usr/bin/env python3
"""Check a generated protocol-2 SD card image, read-only.

The card as it will be flashed: MBR layout and fixed disk signature, both
seeded U-Boot environment copies, the slot boot script and the pinned trust
root on BOOT (with no shared Linux Image there), and each ROOT slot's own
kernel, DTB and modules (tools/check_slot_linux.py). Structural only: it
does not show that a Pi boots the card; that is the hardware acceptance.
"""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import sys
import zlib

sys.dont_write_bytecode = True
from check_slot_linux import check_rootfs  # noqa: E402
from os_bootstrap import HEADROOM, PAYLOAD, SIZE, canonical, strict_json, verify_policy  # noqa: E402

MIB = 1024 * 1024
SECTOR = 512
DISK_SIGNATURE = 0x45504C4B
ENV_OFFSETS = (0x400000, 0x404000)
BOOT_START = 8 * MIB
SLOT_BYTES = 1536 * MIB
BOOT_REQUIRED = ('bootcode.bin', 'start.elf', 'fixup.dat', 'config.txt', 'u-boot.bin', 'boot.scr',
                 'os-index-root.pem')
BOOT_FORBIDDEN = ('Image',)
# Present only while a job runs; a fresh card has none (boot-slot.cmd halts on orphans).
TRANSACTION = ('epk_os_pending', 'epk_os_target', 'epk_os_from', 'epk_os_target_sequence',
               'epk_os_target_sha256', 'epk_os_phase', 'epk_os_trial_started')


def read_at(image, offset, size):
    with Path(image).open('rb') as stream:
        stream.seek(offset)
        data = stream.read(size)
    if len(data) != size:
        raise ValueError('image is truncated')
    return data


def partitions(image):
    """The four primary MBR entries: (type, bootable, start byte, length bytes)."""
    mbr = read_at(image, 0, SECTOR)
    if mbr[510:512] != b'\x55\xaa':
        raise ValueError('no MBR boot signature')
    if struct.unpack_from('<I', mbr, 440)[0] != DISK_SIGNATURE:
        raise ValueError('disk signature is not 0x45504c4b (PARTUUIDs 45504c4b-0N)')
    result = []
    for index in range(4):
        status, kind, start, sectors = struct.unpack_from('<B3xB3xII', mbr, 446 + 16 * index)
        if not kind or not sectors:
            raise ValueError(f'partition {index + 1} missing')
        result.append((kind, status == 0x80, start * SECTOR, sectors * SECTOR))
    return result


def check_layout(image, parts):
    (boot_type, boot_flag, boot_start, _), root_a, root_b, data = parts
    if boot_type != 0x0C or not boot_flag or boot_start != BOOT_START:
        raise ValueError('BOOT must be the bootable FAT32 LBA partition at 8 MiB')
    for name, (kind, flag, start, length) in (('ROOT-A', root_a), ('ROOT-B', root_b)):
        if kind != 0x83 or flag or length != SLOT_BYTES:
            raise ValueError(f'{name} must be a 1536 MiB Linux partition')
    if data[0] != 0x83 or data[1]:
        raise ValueError('DATA must be a Linux partition')
    ends = [start + length for _, _, start, length in parts]
    starts = [start for _, _, start, _ in parts]
    if any(starts[i + 1] < ends[i] for i in range(3)) or ENV_OFFSETS[1] + SIZE > BOOT_START:
        raise ValueError('partitions overlap each other or the environment')
    if Path(image).stat().st_size < ends[-1]:
        raise ValueError('image ends before its last partition')


def parse_env(record):
    crc, flag = struct.unpack_from('<IB', record)
    data = record[5:]
    if len(record) != SIZE or zlib.crc32(data) != crc:
        raise ValueError('environment copy has a bad CRC')
    values, position = {}, 0
    while data[position]:
        end = data.index(0, position)
        name, sep, value = data[position:end].decode('ascii').partition('=')
        if not sep or name in values:
            raise ValueError('malformed environment entry')
        values[name] = value
        position = end + 1
    return flag, values, position + 1


def check_env(image, seed, platform, root_pem):
    copies = [read_at(image, offset, SIZE) for offset in ENV_OFFSETS]
    if seed is not None:
        for name, actual in zip(('env-a.bin', 'env-b.bin'), copies):
            if (Path(seed) / name).read_bytes() != actual:
                raise ValueError(f'card environment differs from seeded {name}')
    (flag_a, env_a, used), (flag_b, env_b, _) = parse_env(copies[0]), parse_env(copies[1])
    if (flag_a, flag_b) != (1, 2) or env_a != env_b:
        raise ValueError('environment copies must hold the same state (flags 1 and 2)')
    if PAYLOAD - used < HEADROOM:
        raise ValueError('environment leaves less than 2 KiB free')
    expected = {'BOOT_ORDER': 'A B', 'BOOT_A_LEFT': '3', 'BOOT_B_LEFT': '3', 'epk_state_schema': '1',
                'epk_state_generation': '0', 'epk_os_floor': str(platform['os_sequence'])}
    for name, value in expected.items():
        if env_a.get(name) != value:
            raise ValueError(f'seeded {name} must be {value}')
    if any(name in env_a for name in TRANSACTION):
        raise ValueError('a fresh card carries no update transaction')
    policy_raw = base64.b64decode(env_a['epk_trust_policy'], validate=True)
    signature = base64.b64decode(env_a['epk_trust_policy_sig'], validate=True)
    policy = verify_policy(policy_raw, signature, root_pem, board=platform['board'], channel=platform['channel'])
    if env_a.get('epk_trust_epoch') != str(policy['trust_epoch']):
        raise ValueError('seeded trust epoch differs from the signed policy')
    return {'env_free_bytes': PAYLOAD - used, 'trust_epoch': policy['trust_epoch'],
            'policy_sha256': hashlib.sha256(policy_raw).hexdigest()}


def fat(image, offset, *args):
    return subprocess.run([*args[:1], '-i', f'{image}@@{offset}', *args[1:]],
                          capture_output=True, timeout=60)


def boot_file(image, offset, name):
    result = fat(image, offset, 'mtype', f'::{name}')
    if result.returncode:
        raise ValueError(f'BOOT is missing {name}')
    return result.stdout


def script_payload(uimage):
    """The script inside a legacy U-Boot script image (mkimage -T script)."""
    magic, header_crc, _, size, _, _, data_crc, _, _, kind, _ = struct.unpack_from('>7I4B', uimage)
    header = bytearray(uimage[:64])
    header[4:8] = b'\0\0\0\0'
    if magic != 0x27051956 or kind != 6 or zlib.crc32(bytes(header)) != header_crc:
        raise ValueError('boot.scr is not a U-Boot script image')
    data = uimage[64:64 + size]
    if len(data) != size or zlib.crc32(data) != data_crc:
        raise ValueError('boot.scr data is truncated or corrupt')
    length, terminator = struct.unpack_from('>II', data)
    if terminator != 0 or 8 + length > size:
        raise ValueError('boot.scr script length is invalid')
    return data[8:8 + length]


def check_boot(image, boot_script, root_pem):
    for name in BOOT_REQUIRED:
        boot_file(image, BOOT_START, name)
    for name in BOOT_FORBIDDEN:
        if fat(image, BOOT_START, 'mdir', f'::{name}').returncode == 0:
            raise ValueError(f'BOOT must not carry a shared Linux {name}')
    if script_payload(boot_file(image, BOOT_START, 'boot.scr')) != Path(boot_script).read_bytes():
        raise ValueError('boot.scr is not the protocol-2 slot script')
    if boot_file(image, BOOT_START, 'os-index-root.pem') != root_pem:
        raise ValueError('BOOT trust root differs from the seeded root')
    if b'arm_64bit=1' not in boot_file(image, BOOT_START, 'config.txt').splitlines():
        raise ValueError('config.txt is not 64-bit')


def same_bytes(image, offset, path):
    path = Path(path)
    size = path.stat().st_size
    with Path(image).open('rb') as card, path.open('rb') as source:
        card.seek(offset)
        remaining = size
        while remaining:
            chunk = source.read(min(MIB, remaining))
            if not chunk or card.read(len(chunk)) != chunk:
                return False
            remaining -= len(chunk)
    return True


def check_slots(image, parts, rootfs_a, rootfs_b):
    identities = []
    for name, (_, _, start, length), rootfs in (('ROOT-A', parts[1], rootfs_a), ('ROOT-B', parts[2], rootfs_b)):
        if Path(rootfs).stat().st_size > length or not same_bytes(image, start, rootfs):
            raise ValueError(f'{name} on the card differs from {rootfs}')
        identities.append(check_rootfs(rootfs))
    if identities[0] != identities[1]:
        raise ValueError('ROOT-A and ROOT-B carry different Linux')
    return identities[0]


def check_card(image, rootfs_a, rootfs_b, boot_script, seed=None):
    image = Path(image).resolve(strict=True)
    if not image.is_file():
        raise ValueError('card input must be a regular image file, not a device')
    parts = partitions(image)
    check_layout(image, parts)
    root_pem = boot_file(image, BOOT_START, 'os-index-root.pem')
    if seed is not None and (Path(seed) / 'os-index-root.pem').read_bytes() != root_pem:
        raise ValueError('BOOT trust root differs from the seed')
    slot = check_slots(image, parts, rootfs_a, rootfs_b)
    with subprocess.Popen(['debugfs', '-R', 'cat /usr/lib/epilykos/platform.json', str(rootfs_a)],
                          stdout=subprocess.PIPE, stderr=subprocess.DEVNULL) as process:
        platform = strict_json(process.stdout.read(4097), 4096)
    if seed is not None and canonical(platform) != (Path(seed) / 'platform.json').read_bytes():
        raise ValueError('seeded platform differs from the slots')
    env = check_env(image, seed, platform, root_pem)
    check_boot(image, boot_script, root_pem)
    return {'schema': 1, 'os_sequence': platform['os_sequence'], 'channel': platform['channel'],
            'kernel': slot['kernel'], 'image_sha256': slot['image_sha256'], 'dtb_sha256': slot['dtb_sha256'],
            'modules_tree_sha256': slot['modules_tree_sha256'], **env}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--image', type=Path, required=True)
    parser.add_argument('--rootfs-a', type=Path, required=True)
    parser.add_argument('--rootfs-b', type=Path, required=True)
    parser.add_argument('--boot-script', type=Path, required=True)
    parser.add_argument('--seed', type=Path, help='the os_bootstrap.py output the card was built from')
    args = parser.parse_args()
    try:
        result = check_card(args.image, args.rootfs_a, args.rootfs_b, args.boot_script, args.seed)
    except (ValueError, OSError, KeyError, subprocess.SubprocessError) as exc:
        parser.exit(1, f'card image refused: {exc}\n')
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    main()
