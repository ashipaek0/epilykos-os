#!/usr/bin/env python3
"""Protocol-2 image integration: the real post-build block, post-image.sh,
genimage-protocol2.cfg and tools/check_card_image.py on small fixtures.

Builds a card the way Buildroot does (staged slot, packed ext4, seeded
environment from test-only trust), then checks it and refuses tampered
cards. Needs genimage, mtools, dosfstools, e2fsprogs, mkimage and OpenSSL.
No physical device is opened and nothing here shows that a Pi boots it.
"""
import base64
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
BOARD = ROOT / 'os/board/epilykos/raspberrypi3'
sys.path.insert(0, str(ROOT / 'tools'))
from test_slot_linux import dtb, module  # noqa: E402
import check_card_image  # noqa: E402

DEFAULTS = b'arch=arm\nbaudrate=115200\nbootdelay=2\nfdt_addr_r=0x02600000\nkernel_addr_r=0x00080000\n'


def run(*args, **kwargs):
    return subprocess.run([str(a) for a in args], check=True, capture_output=True, **kwargs)


def protocol2_block(script):
    """The protocol-2 section of post-build.sh, exactly as Buildroot runs it."""
    text = Path(script).read_text()
    return text[text.index('# -- Protocol 2: this slot'):]


class CardImageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        missing = [tool for tool in ('genimage', 'mcopy', 'mtype', 'mdir', 'mkfs.vfat', 'mkimage', 'debugfs')
                   if not shutil.which(tool) and not Path('/usr/sbin', tool).exists()]
        if missing:
            raise unittest.SkipTest('missing host tools: ' + ', '.join(missing))
        cls.temp = tempfile.TemporaryDirectory()
        cls.work = Path(cls.temp.name)
        cls.trust = cls.work / 'trust'
        run(sys.executable, ROOT / 'tools/os_test_trust.py', '--output', cls.trust, '--channel', 'dev')
        cls.card = cls.build(cls.work / 'build')

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    @classmethod
    def build(cls, base, **env_overrides):
        """Stage a slot with post-build's block, then run post-image.sh."""
        target, binaries = base / 'target', base / 'images'
        modules = target / 'lib/modules/6.6.31'
        (modules / 'kernel').mkdir(parents=True)
        (modules / 'kernel/driver.ko').write_bytes(module('6.6.31'))
        (modules / 'modules.dep').write_text('kernel/driver.ko:\n')
        app = target / 'usr/lib/epilykos'
        app.mkdir(parents=True)
        (app / 'image.env').write_text('EPILYKOS_CHANNEL=dev\nEPILYKOS_RELEASE=3.0-dev.1\nEPILYKOS_SEQUENCE=10\n'
                                       'EPILYKOS_IMAGE_REF=docker.io/ashipaek0/epilykos@sha256:' + 'a' * 64 +
                                       '\nEPILYKOS_IMAGE_ID=' + 'c' * 64 + '\nEPILYKOS_OS_VERSION=0.7-dev.1\n')
        firmware = binaries / 'rpi-firmware'
        firmware.mkdir(parents=True)
        for name in ('bootcode.bin', 'start.elf', 'fixup.dat'):
            (firmware / name).write_bytes(b'fixture ' + name.encode())
        shutil.copy(BOARD / 'config.txt', firmware / 'config.txt')
        (binaries / 'u-boot.bin').write_bytes(b'fixture loader')
        kernel = bytearray(128)
        kernel[56:60] = b'ARM\x64'
        kernel.extend(b'Linux version 6.6.31 (fixture)\0')
        (binaries / 'Image').write_bytes(kernel)
        (binaries / 'bcm2837-rpi-3-b.dtb').write_bytes(dtb())
        (binaries / 'u-boot-initial-env').write_bytes(DEFAULTS)
        run('mkimage', '-A', 'arm64', '-T', 'script', '-C', 'none', '-d', BOARD / 'boot-slot.cmd',
            binaries / 'boot.scr')
        config = base / '.config'
        protocol1 = env_overrides.pop('_protocol1', None)
        config.write_text('BR2_EPILYKOS_NATIVE_ENV_GUARD=y\n' if protocol1 else 'BR2_EPILYKOS_BOOT_PROTOCOL_2=y\n')
        env_overrides.pop('BR2_CONFIG', None)
        env = {**os.environ, 'BR2_CONFIG': str(config), 'TARGET_DIR': str(target), 'BINARIES_DIR': str(binaries),
               'EPILYKOS_OS_SEQUENCE': '19', 'EPILYKOS_SOURCE_COMMIT': 'b' * 40,
               'EPILYKOS_TRUST_DIR': str(cls.trust), **env_overrides}
        block = ('set -e\nCHANNEL=dev\nOS_VERSION=0.7-dev.1\nRELEASE=3.0-dev.1\nREPO_DIR=' + str(ROOT) +
                 '\nBOARD_DIR=' + str(BOARD) + '\n'
                 + protocol2_block(BOARD / 'post-build.sh'))
        run('sh', '-c', block, env=env)
        # Buildroot packs the staged target into rootfs.ext4 between the hooks.
        run('/usr/sbin/mke2fs', '-q', '-t', 'ext4', '-O', '^64bit,^metadata_csum_seed,^orphan_file',
            '-d', target, '-L', 'ROOT-A', '-F', binaries / 'rootfs.ext4', '32768')
        run('sh', BOARD / 'post-image.sh', env=env)
        return binaries

    def check(self, image, **kwargs):
        binaries = self.card
        args = {'rootfs_a': binaries / 'rootfs.ext4', 'rootfs_b': binaries / 'rootfs-b.ext4',
                'boot_script': BOARD / 'boot-slot.cmd', 'seed': binaries / 'protocol2-seed', **kwargs}
        return check_card_image.check_card(image, **args)

    def copy(self):
        path = self.work / 'mutated.img'
        if path.exists():
            path.unlink()
        run('cp', '--sparse=always', self.card / 'sdcard.img', path)
        return path

    def boot_offset(self, image):
        return f'{image}@@{check_card_image.BOOT_START}'

    def test_built_card_has_seeded_state_and_slot_linux(self):
        result = self.check(self.card / 'sdcard.img')
        self.assertEqual((result['os_sequence'], result['channel'], result['kernel']), (19, 'dev', '6.6.31'))
        self.assertEqual(result['trust_epoch'], 1)
        self.assertGreaterEqual(result['env_free_bytes'], 2048)
        platform = json.loads((self.card / 'protocol2-seed/platform.json').read_text())
        target = self.card.parent / 'target'
        self.assertTrue((target / 'usr/lib/systemd/system-preset/40-epilykos-protocol2.preset').is_file())
        self.assertTrue((target / 'usr/lib/systemd/system.conf.d/50-epilykos-watchdog.conf').is_file())
        self.assertEqual((platform['boot_protocol'], platform['host_api'], platform['source']), (2, 2, 'b' * 40))
        # post-image.sh ran the same check itself (CLI path).
        cli = subprocess.run([sys.executable, ROOT / 'tools/check_card_image.py', '--image', self.card / 'sdcard.img',
                              '--rootfs-a', self.card / 'rootfs.ext4', '--rootfs-b', self.card / 'rootfs-b.ext4',
                              '--boot-script', BOARD / 'boot-slot.cmd'], capture_output=True, text=True)
        self.assertEqual(cli.returncode, 0, cli.stderr)

    def test_environment_copies_are_both_seeded_and_selectable(self):
        image = self.card / 'sdcard.img'
        a, b = (check_card_image.read_at(image, offset, 16384) for offset in check_card_image.ENV_OFFSETS)
        self.assertEqual((a[4], b[4]), (1, 2))
        self.assertEqual(a[5:], b[5:])
        _, values, _ = check_card_image.parse_env(a)
        self.assertEqual((values['epk_os_floor'], values['epk_state_generation'], values['BOOT_ORDER']),
                         ('19', '0', 'A B'))
        self.assertEqual(base64.b64decode(values['epk_trust_policy']), (self.trust / 'os-policy.json').read_bytes())
        self.assertEqual(values['baudrate'], '115200', 'starts from the loader\'s generated defaults')

    def test_compiled_guard_selects_the_seeded_card(self):
        # The C guard U-Boot and fw-tools run, reading the card image itself.
        core = BOARD / 'platform-env'
        driver = self.work / 'env-driver'
        if not driver.exists():
            run('cc', '-std=c99', '-O2', '-Wall', '-Wextra', '-Werror', '-I', core, core / 'epk_env.c',
                ROOT / 'tools/platform_env_test_driver.c', '-o', driver)
        image = self.copy()
        result = subprocess.run([str(driver), 'select', str(image)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        # copy B (flag 2) wins; generation 0, floor 19, trust epoch 1
        self.assertEqual(result.stdout.split(), ['1', '0', '19', '1'])

    def test_tampered_environment_is_refused(self):
        image = self.copy()
        with image.open('r+b') as stream:
            stream.seek(check_card_image.ENV_OFFSETS[1] + 100)
            byte = stream.read(1)
            stream.seek(-1, 1)
            stream.write(bytes([byte[0] ^ 1]))
        with self.assertRaisesRegex(ValueError, 'env|CRC'):
            self.check(image)

    def test_blank_environment_is_refused(self):
        image = self.copy()
        with image.open('r+b') as stream:
            stream.seek(check_card_image.ENV_OFFSETS[0])
            stream.write(b'\0' * 32768)
        with self.assertRaises(ValueError):
            self.check(image, seed=None)

    def test_policy_not_signed_by_the_boot_root_is_refused(self):
        image = self.copy()
        other = self.work / 'other-trust'
        if not other.exists():
            run(sys.executable, ROOT / 'tools/os_test_trust.py', '--output', other)
        run('mcopy', '-o', '-i', self.boot_offset(image), other / 'os-index-root.pem', '::os-index-root.pem')
        with self.assertRaisesRegex(ValueError, 'root|verif|signature'):
            self.check(image, seed=None)

    def test_shared_kernel_on_boot_is_refused(self):
        image = self.copy()
        run('mcopy', '-i', self.boot_offset(image), self.card / 'Image', '::Image')
        with self.assertRaisesRegex(ValueError, 'shared Linux Image'):
            self.check(image)

    def test_protocol1_boot_script_is_refused(self):
        image = self.copy()
        scr = self.work / 'p1.scr'
        run('mkimage', '-A', 'arm64', '-T', 'script', '-C', 'none', '-d', BOARD / 'boot.cmd', scr)
        run('mcopy', '-o', '-i', self.boot_offset(image), scr, '::boot.scr')
        with self.assertRaisesRegex(ValueError, 'slot script'):
            self.check(image)

    def test_slot_differing_from_its_rootfs_is_refused(self):
        image = self.copy()
        start = check_card_image.partitions(image)[2][2]
        with image.open('r+b') as stream:
            stream.seek(start + 1024 + 600)
            byte = stream.read(1)
            stream.seek(-1, 1)
            stream.write(bytes([byte[0] ^ 0xff]))
        with self.assertRaisesRegex(ValueError, 'ROOT-B'):
            self.check(image)

    def test_wrong_disk_signature_is_refused(self):
        image = self.copy()
        with image.open('r+b') as stream:
            stream.seek(440)
            stream.write(struct.pack('<I', 0))
        with self.assertRaisesRegex(ValueError, 'signature'):
            self.check(image)

    def test_script_image_parser_checks_crc(self):
        raw = bytearray((self.card / 'boot.scr').read_bytes())
        self.assertEqual(check_card_image.script_payload(bytes(raw)), (BOARD / 'boot-slot.cmd').read_bytes())
        raw[-1] ^= 1
        with self.assertRaisesRegex(ValueError, 'corrupt'):
            check_card_image.script_payload(bytes(raw))

    def test_build_without_trust_or_sequence_stops(self):
        for overrides, message in (({'EPILYKOS_TRUST_DIR': str(self.work / 'missing')}, b'os-policy.json'),
                                   ({'EPILYKOS_OS_SEQUENCE': ''}, b'EPILYKOS_OS_SEQUENCE'),
                                   ({'EPILYKOS_OS_SEQUENCE': '07'}, b'EPILYKOS_OS_SEQUENCE')):
            base = Path(tempfile.mkdtemp(dir=self.work))
            with self.assertRaises(subprocess.CalledProcessError) as caught:
                self.build(base, **overrides)
            self.assertIn(message, caught.exception.stderr)

    def test_protocol2_layout_differs_from_protocol1_only_as_intended(self):
        def body(path):
            lines = [line.strip() for line in path.read_text().splitlines()]
            return [line for line in lines if line and not line.startswith('#')]
        p1, p2 = body(BOARD / 'genimage.cfg'), body(BOARD / 'genimage-protocol2.cfg')
        removed = [line for line in p1 if line not in p2]
        added = [line for line in p2 if line not in p1]
        self.assertEqual(removed, ['"Image",'])
        self.assertEqual(added, ['"os-index-root.pem",', 'partition uboot-env-a {', 'in-partition-table = false',
                                 'offset = 4M', 'image = "env-a.bin"', 'size = 16K', 'partition uboot-env-b {',
                                 'in-partition-table = false', 'offset = 4112K', 'image = "env-b.bin"', 'size = 16K'])

    def test_protocol1_build_is_unchanged(self):
        # Without the switch: post-build skips its block, post-image uses
        # genimage.cfg, the environment area ships blank and BOOT keeps Image.
        base = self.work / 'p1'
        binaries = self.build(base, BR2_CONFIG=str(self.work / 'p1.config'), _protocol1='1')
        image = binaries / 'sdcard.img'
        self.assertFalse((binaries / 'protocol2-seed').exists())
        self.assertFalse((base / 'target/usr/lib/epilykos/platform.json').exists())
        self.assertFalse((base / 'target/usr/lib/systemd/system-preset/40-epilykos-protocol2.preset').exists())
        self.assertEqual(check_card_image.read_at(image, 0x400000, 32768), b'\0' * 32768)
        run('mdir', '-i', self.boot_offset(image), '::Image')
        with self.assertRaises(ValueError):
            self.check(image, seed=None)

if __name__ == '__main__':
    unittest.main()
