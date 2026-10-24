#!/usr/bin/env python3
"""Exercise slot artifacts and actual ext4 bytes; no kernel boot is implied."""
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def module(release, machine=183):
    # Minimal independent ELF64 ET_REL with real .modinfo section framing.
    data = bytearray(448)
    ident = b'\x7fELF\x02\x01\x01' + b'\0' * 9
    data[:64] = struct.pack('<16sHHIQQQIHHHHHH', ident, 1, machine, 1, 0, 0, 256, 0, 64, 0, 0, 64, 3, 1)
    names = b'\0.shstrtab\0.modinfo\0'
    info = b'vermagic=' + release.encode() + b' SMP mod_unload aarch64\0'
    data[64:64 + len(names)] = names
    data[128:128 + len(info)] = info
    data[320:384] = struct.pack('<IIQQQQIIQQ', 1, 3, 0, 0, 64, len(names), 0, 0, 1, 0)
    data[384:448] = struct.pack('<IIQQQQIIQQ', 11, 1, 0, 0, 128, len(info), 0, 0, 1, 0)
    return bytes(data)


def dtb():
    # FDT v17: a root node with compatible strings and proper END token.
    strings = b'compatible\0'
    value = b'raspberrypi,3-model-b\0brcm,bcm2837\0'
    structure = struct.pack('>I', 1) + b'\0' * 4 + struct.pack('>III', 3, len(value), 0) + value
    structure += b'\0' * (-len(structure) % 4) + struct.pack('>II', 2, 9)
    total = 56 + len(structure) + len(strings)
    return struct.pack('>10I', 0xd00dfeed, total, 56, 56 + len(structure), 40, 17, 16, 0,
                       len(strings), len(structure)) + b'\0' * 16 + structure + strings


class SlotLinuxTests(unittest.TestCase):
    def setUp(self):
        path = ROOT / 'tools/slot_linux.py'
        self.assertTrue(path.is_file(), 'complete-slot Linux artifact validator is missing')
        spec = importlib.util.spec_from_file_location('slot_linux', path)
        self.api = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.api)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)
        self.target = self.work / 'target'
        self.base = self.target / 'usr/lib/epilykos'
        self.base.mkdir(parents=True)
        self.kernel = self.work / 'Image'
        image = bytearray(128)
        image[56:60] = b'ARM\x64'
        image.extend(b'Linux version 6.6.31 (fixture)\0')
        self.kernel.write_bytes(image)
        self.dtb = self.work / 'bcm2837-rpi-3-b.dtb'
        self.dtb.write_bytes(dtb())
        self.modules = self.target / 'lib/modules/6.6.31'
        (self.modules / 'kernel').mkdir(parents=True)
        (self.modules / 'kernel/driver.ko').write_bytes(module('6.6.31'))
        (self.modules / 'modules.dep').write_text('kernel/driver.ko:\n')
        self.platform = {'schema': 1, 'board': 'raspberrypi3', 'arch': 'arm64', 'slot_layout': 'pi3-mbr-v1',
                         'boot_protocol': 2, 'channel': 'dev', 'os_version': '0.7-dev.1', 'os_sequence': 19,
                         'source': 'b' * 40, 'kernel': '6.6.31', 'host_api': 2, 'app_release': '3.0-dev.1'}
        (self.base / 'image.env').write_text('EPILYKOS_CHANNEL=dev\nEPILYKOS_RELEASE=3.0-dev.1\n'
            'EPILYKOS_SEQUENCE=10\nEPILYKOS_IMAGE_REF=docker.io/ashipaek0/epilykos@sha256:' + 'a' * 64 +
            '\nEPILYKOS_IMAGE_ID=' + 'c' * 64 + '\nEPILYKOS_OS_VERSION=0.7-dev.1\n')

    def stage(self):
        return self.api.stage(self.target, self.kernel, self.dtb, self.platform)

    def test_slot_artifacts_match_modules(self):
        self.stage()
        self.assertEqual((self.base / 'boot/Image').read_bytes(), self.kernel.read_bytes())
        self.assertEqual((self.base / 'boot/bcm2837-rpi-3-b.dtb').read_bytes(), self.dtb.read_bytes())
        self.assertEqual(json.loads((self.base / 'platform.json').read_bytes()), self.platform)
        manifest = self.api.verify_tree(self.target)
        self.assertEqual(manifest['kernel'], '6.6.31')
        self.assertEqual(manifest['module_count'], 1)
        self.assertEqual(manifest['image_sha256'], hashlib.sha256(self.kernel.read_bytes()).hexdigest())
        self.assertFalse((self.target / 'boot/Image').exists())

    def test_wrong_module_vermagic_or_architecture_is_refused_before_staging(self):
        for data in [module('6.6.32'), module('6.6.31', machine=62), b'not ELF']:
            (self.modules / 'kernel/driver.ko').write_bytes(data)
            with self.subTest(data=data[:20]), self.assertRaises(ValueError):
                self.stage()
            self.assertFalse((self.base / 'platform.json').exists())

    def test_kernel_release_and_dtb_are_validated(self):
        original = self.kernel.read_bytes()
        for bad in [original.replace(b'6.6.31', b'6.6.32'), original[:58], original.replace(b'ARM\x64', b'x86!')]:
            self.kernel.write_bytes(bad)
            with self.assertRaises(ValueError):
                self.stage()
        self.kernel.write_bytes(original)
        for bad in [dtb()[:-1], dtb().replace(b'raspberrypi,3-model-b', b'raspberrypi,4-model-b'), b'bad DTB']:
            self.dtb.write_bytes(bad)
            with self.assertRaises(ValueError):
                self.stage()

    def test_changed_missing_or_extra_modules_are_detected_in_staged_tree(self):
        self.stage()
        path = self.modules / 'kernel/driver.ko'
        original = path.read_bytes()
        path.write_bytes(original + b'changed module content')
        with self.assertRaises(ValueError):
            self.api.verify_tree(self.target)
        path.unlink()
        with self.assertRaises(ValueError):
            self.api.verify_tree(self.target)
        path.write_bytes(original)
        (self.modules / 'kernel/extra.ko').write_bytes(original)
        with self.assertRaises(ValueError):
            self.api.verify_tree(self.target)

    def test_merged_usr_and_symlink_escape(self):
        (self.target / 'usr/lib/modules').mkdir()
        self.modules.rename(self.target / 'usr/lib/modules/6.6.31')
        (self.target / 'lib/modules').rmdir()
        (self.target / 'lib').rmdir()
        (self.target / 'lib').symlink_to('usr/lib')
        self.stage()
        self.assertEqual(self.api.verify_tree(self.target)['module_directory'], 'usr/lib/modules/6.6.31')
        path = self.target / 'usr/lib/modules/6.6.31/kernel/driver.ko'
        path.unlink()
        path.symlink_to(self.kernel)
        with self.assertRaises(ValueError):
            self.api.verify_tree(self.target)

    def test_stale_module_release_or_app_identity_is_rejected(self):
        (self.target / 'lib/modules/6.6.30').mkdir()
        with self.assertRaises(ValueError):
            self.stage()
        (self.target / 'lib/modules/6.6.30').rmdir()
        self.platform['app_release'] = '3.0-dev.2'
        with self.assertRaises(ValueError):
            self.stage()

    def test_packed_slot_cannot_hide_an_extra_module_release(self):
        self.stage()
        image = self.work / 'rootfs.ext4'
        subprocess.run(['/usr/sbin/mke2fs', '-q', '-t', 'ext4', '-O', '^64bit,^metadata_csum_seed,^orphan_file',
                        '-d', str(self.target), '-L', 'ROOT-A', '-F', str(image), '32768'],
                       check=True, capture_output=True)
        subprocess.run(['debugfs', '-w', '-R', 'mkdir /lib/modules/6.6.30', str(image)],
                       check=True, capture_output=True)
        result = subprocess.run([sys.executable, str(ROOT / 'tools/check_slot_linux.py'), '--rootfs', str(image)],
                                capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0, result.stdout)

    def test_packed_merged_usr_uses_the_actual_runtime_module_path(self):
        (self.target / 'usr/lib/modules').mkdir()
        self.modules.rename(self.target / 'usr/lib/modules/6.6.31')
        (self.target / 'lib/modules').rmdir()
        (self.target / 'lib').rmdir()
        (self.target / 'lib').symlink_to('usr/lib')
        self.stage()
        image = self.work / 'merged.ext4'
        subprocess.run(['/usr/sbin/mke2fs', '-q', '-t', 'ext4', '-O', '^64bit,^metadata_csum_seed,^orphan_file',
                        '-d', str(self.target), '-F', str(image), '32768'], check=True, capture_output=True)
        checker = ROOT / 'tools/check_slot_linux.py'
        valid = subprocess.run([sys.executable, str(checker), '--rootfs', str(image)], capture_output=True)
        self.assertEqual(valid.returncode, 0, valid.stderr)
        for command in ['rm /lib', 'mkdir /lib', 'mkdir /lib/modules', 'mkdir /lib/modules/WRONG-RELEASE']:
            subprocess.run(['debugfs', '-w', '-R', command, str(image)], check=True, capture_output=True)
        invalid = subprocess.run([sys.executable, str(checker), '--rootfs', str(image)], capture_output=True)
        self.assertNotEqual(invalid.returncode, 0, invalid.stdout)

    def test_multiple_dtb_roots_are_not_a_valid_complete_tree(self):
        raw = dtb()
        # Insert a second root immediately before END, updating FDT lengths.
        fields = list(struct.unpack('>10I', raw[:40]))
        extra = struct.pack('>I', 1) + b'\0' * 4 + struct.pack('>I', 2)
        insertion = fields[2] + fields[9] - 4
        fields[1] += len(extra)
        fields[3] += len(extra)
        fields[9] += len(extra)
        bad = struct.pack('>10I', *fields) + raw[40:insertion] + extra + raw[insertion:]
        self.dtb.write_bytes(bad)
        with self.assertRaises(ValueError):
            self.stage()

    def test_generated_ext4_bytes_not_target_directory_are_verified(self):
        self.stage()
        image = self.work / 'rootfs.ext4'
        subprocess.run(['/usr/sbin/mke2fs', '-q', '-t', 'ext4', '-O', '^64bit,^metadata_csum_seed,^orphan_file',
                        '-d', str(self.target), '-L', 'ROOT-A', '-F', str(image), '32768'],
                       check=True, capture_output=True)
        checker = ROOT / 'tools/check_slot_linux.py'
        self.assertTrue(checker.is_file(), 'generated ext4 slot checker is missing')
        result = subprocess.run([sys.executable, str(checker), '--rootfs', str(image)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        # Modify on-image kernel while output/target stays untouched.
        bad = self.work / 'bad-image'
        bad.write_bytes(b'wrong kernel bytes')
        result = subprocess.run(['debugfs', '-w', '-R', f'rm /usr/lib/epilykos/boot/Image', str(image)], capture_output=True)
        self.assertEqual(result.returncode, 0)
        subprocess.run(['debugfs', '-w', '-R', f'write {bad} /usr/lib/epilykos/boot/Image', str(image)],
                       check=True, capture_output=True)
        result = subprocess.run([sys.executable, str(checker), '--rootfs', str(image)], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0, result.stdout)


if __name__ == '__main__':
    unittest.main()
