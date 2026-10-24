#!/usr/bin/env python3
"""Run the experimental protocol2 script in patched U-Boot/file-backed MMC.

Only kernel execution and CPU reset are test endpoints; native env import,
replicated saves, ext4 loading and hush control flow are the real loader code.
"""
import argparse
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest

from test_platform_env import OFFSETS, SIZE, decode, record, state
from test_slot_linux import dtb

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'os/board/epilykos/raspberrypi3/boot-slot.cmd'
UBOOT = None
MKIMAGE = None


class SlotBootTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if UBOOT is None:
            raise unittest.SkipTest('run make test-slot-boot to build the pinned sandbox')
        cls.temp = tempfile.TemporaryDirectory()
        cls.base = Path(cls.temp.name)
        cls.images = {}
        for slot in ['A', 'B']:
            target = cls.base / slot / 'usr/lib/epilykos/boot'
            target.mkdir(parents=True)
            data = bytearray(128)
            data[0] = ord(slot)
            data[56:60] = b'ARM\x64'
            (target / 'Image').write_bytes(data)
            tree = bytearray(dtb())
            tree[31] = ord(slot)  # Distinct fixture data, never executed as a real DTB.
            (target / 'bcm2837-rpi-3-b.dtb').write_bytes(tree)
            image = cls.base / f'root-{slot}.ext4'
            subprocess.run(['/usr/sbin/mke2fs', '-q', '-t', 'ext4', '-O', '^64bit,^metadata_csum_seed,^orphan_file',
                            '-d', str(cls.base / slot), '-F', str(image), '32768'], check=True, capture_output=True)
            cls.images[slot] = image

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, 'temp'):
            cls.temp.cleanup()

    def setUp(self):
        self.assertTrue(SCRIPT.is_file(), 'checked slot boot script is missing')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)
        self.card = self.work / 'card.img'
        # Same disk signature, env offsets and ROOT partition numbers as Pi3.
        entries = [(0x0c, 16384, 2048), (0x83, 18432, 65536), (0x83, 83968, 65536), (0x83, 149504, 2048)]
        mbr = bytearray(512)
        struct.pack_into('<I', mbr, 440, 0x45504c4b)
        for index, (kind, start, count) in enumerate(entries):
            mbr[446 + 16 * index:462 + 16 * index] = struct.pack('<B3sB3sII', 0, b'\0'*3, kind, b'\0'*3, start, count)
        mbr[510:] = b'\x55\xaa'
        with self.card.open('wb') as stream:
            stream.write(mbr)
            for slot, start in [('A', 18432), ('B', 83968)]:
                stream.seek(start * 512)
                with self.images[slot].open('rb') as source:
                    shutil.copyfileobj(source, stream)
            stream.truncate(151552 * 512)
        self.values = state(generation='0', floor='17', epoch='2')
        self.seed()
        self.script = self.work / 'boot.scr'
        subprocess.run([str(MKIMAGE), '-A', 'arm64', '-T', 'script', '-C', 'none', '-d', str(SCRIPT), str(self.script)],
                       check=True, capture_output=True)

    def seed(self):
        with self.card.open('r+b') as stream:
            for offset, flag in zip(OFFSETS, [1, 2]):
                stream.seek(offset)
                stream.write(record(self.values, flag))

    def read_env(self):
        with self.card.open('rb') as stream:
            stream.seek(OFFSETS[0])
            a = decode(stream.read(SIZE))
            stream.seek(OFFSETS[1])
            b = decode(stream.read(SIZE))
        self.assertEqual(a, b)
        return a

    def boot(self, setup=''):
        command = (f'setenv kernel_addr_r 1000000;setenv fdt_addr_r 6000000;'
                   f'{setup};host load hostfs - 800000 {self.script};source 800000;'
                   'echo EPK_TEST_FELL_THROUGH')
        result = subprocess.run([str(UBOOT), '-d', str(UBOOT.with_name('u-boot.dtb')), '-c', command], cwd=self.work,
                                capture_output=True, text=True, timeout=15)
        self.assertNotIn('EPK_TEST_FELL_THROUGH', result.stdout, result.stdout + result.stderr)
        return result.stdout + result.stderr

    def remove_file(self, slot, path):
        changed = self.work / f'changed-{slot}.ext4'
        shutil.copyfile(self.images[slot], changed)
        subprocess.run(['debugfs', '-w', '-R', 'rm ' + path, str(changed)], check=True, capture_output=True)
        with self.card.open('r+b') as stream, changed.open('rb') as source:
            stream.seek((18432 if slot == 'A' else 83968) * 512)
            shutil.copyfileobj(source, stream)

    def test_selected_slot_supplies_both_linux_files(self):
        for order, expected in [('A B', 'A'), ('B A', 'B')]:
            with self.subTest(order=order):
                self.values['BOOT_ORDER'] = order
                self.seed()
                output = self.boot()
                self.assertIn(f'EPK_TEST_LAUNCH image={expected} dtb={expected}', output)
                self.assertNotIn('EPK_TEST_RESET', output)
                self.assertEqual(self.read_env()[f'BOOT_{expected}_LEFT'.encode()], b'2')

    def test_returned_kernel_resets_with_attempt_spent(self):
        output = self.boot('setenv test_return_kernel 1')
        self.assertIn('EPK_TEST_LAUNCH image=A dtb=A', output)
        self.assertIn('EPK_TEST_RESET', output)
        self.assertEqual(self.read_env()[b'BOOT_A_LEFT'], b'2')

    def test_missing_dtb_never_reuses_prior_ram(self):
        self.remove_file('A', '/usr/lib/epilykos/boot/bcm2837-rpi-3-b.dtb')
        output = self.boot('mw.b 6000000 42 100')
        self.assertNotIn('EPK_TEST_LAUNCH', output)
        self.assertIn('EPK_TEST_RESET', output)
        self.assertEqual(self.read_env()[b'BOOT_A_LEFT'], b'2')

    def test_counter_save_failure_halts(self):
        output = self.boot('setenv test_fail_mmc_write 1')
        self.assertNotIn('EPK_TEST_LAUNCH', output)
        self.assertNotIn('EPK_TEST_RESET', output)
        self.assertIn('diagnostic halt', output)
        self.assertEqual(self.read_env()[b'BOOT_A_LEFT'], b'3')

    def test_trial_started_persists_before_launch_without_overwriting_confirmation(self):
        job = '11111111-1111-4111-8111-111111111111'
        self.values.update(BOOT_ORDER='B A', epk_os_pending=job, epk_os_target='B', epk_os_from='A',
                           epk_os_target_sequence='18', epk_os_target_sha256='a'*64, epk_os_phase='confirming')
        self.seed()
        output = self.boot()
        self.assertIn('EPK_TEST_LAUNCH image=B dtb=B', output)
        self.assertIn('trial=' + job + ' phase=confirming', output)
        values = self.read_env()
        self.assertEqual(values[b'epk_os_trial_started'], job.encode())
        self.assertEqual(values[b'epk_os_phase'], b'confirming')
        self.assertEqual(values[b'epk_state_generation'], b'1')
        self.boot()
        self.assertEqual(self.read_env()[b'epk_state_generation'], b'1')

    def test_three_failed_load_attempts_then_other_slot(self):
        self.remove_file('A', '/usr/lib/epilykos/boot/Image')
        for remaining in [b'2', b'1', b'0']:
            output = self.boot()
            self.assertNotIn('EPK_TEST_LAUNCH', output)
            self.assertEqual(self.read_env()[b'BOOT_A_LEFT'], remaining)
        self.assertIn('EPK_TEST_LAUNCH image=B dtb=B', self.boot())

    def test_both_exhausted_halts_without_refill(self):
        self.values.update(BOOT_A_LEFT='0', BOOT_B_LEFT='0')
        self.seed()
        output = self.boot()
        self.assertIn('diagnostic halt', output)
        self.assertNotIn('EPK_TEST_RESET', output)
        self.assertEqual(self.read_env()[b'BOOT_A_LEFT'], b'0')
        self.assertEqual(self.read_env()[b'BOOT_B_LEFT'], b'0')

    def test_ready_target_never_boots_from_a_power_cut(self):
        self.values.update(BOOT_ORDER='B A', epk_os_pending='11111111-1111-4111-8111-111111111111',
                           epk_os_target='B', epk_os_from='A', epk_os_target_sequence='18',
                           epk_os_target_sha256='a'*64, epk_os_phase='ready')
        self.seed()
        self.assertIn('EPK_TEST_LAUNCH image=A dtb=A', self.boot())
        self.assertNotIn(b'epk_os_trial_started', self.read_env())

    def test_lost_env_and_malformed_counters_never_enroll_defaults(self):
        for field, value in [('BOOT_ORDER', 'A B A'), ('BOOT_A_LEFT', 'garbage')]:
            self.values[field] = value
            self.seed()
            output = self.boot()
            self.assertNotIn('EPK_TEST_LAUNCH', output)
            self.assertIn('diagnostic halt', output)
            self.values = state(generation='0', floor='17', epoch='2')
        with self.card.open('r+b') as stream:
            for offset in OFFSETS:
                stream.seek(offset)
                stream.write(b'\0' * SIZE)
        output = self.boot()
        self.assertNotIn('EPK_TEST_LAUNCH', output)
        self.assertIn('diagnostic halt', output)

    def test_malformed_pending_identity_halts_before_any_save(self):
        pending = dict(epk_os_pending='11111111-1111-4111-8111-111111111111',
                       epk_os_target='B', epk_os_from='A', epk_os_target_sequence='18',
                       epk_os_target_sha256='a'*64, epk_os_phase='activated')
        for field, value in [('epk_os_pending', 'not-a-job'),
                             ('epk_os_pending', '11111111-1111-1111-8111-111111111111'),
                             ('epk_os_target_sequence', '0'),
                             ('epk_os_target_sequence', '018'),
                             ('epk_os_target_sequence', '18446744073709551616'),
                             ('epk_os_target_sha256', 'A'*64),
                             ('epk_os_target_sha256', 'a'*63),
                             ('epk_os_trial_started', '22222222-2222-4222-8222-222222222222')]:
            with self.subTest(field=field, value=value):
                self.values = state(generation='0', floor='17', epoch='2')
                self.values.update(pending)
                self.values[field] = value
                self.seed()
                before = self.card.read_bytes()[OFFSETS[0]:OFFSETS[1] + SIZE]
                output = self.boot()
                self.assertNotIn('EPK_TEST_LAUNCH', output)
                self.assertIn('diagnostic halt', output)
                self.assertEqual(self.card.read_bytes()[OFFSETS[0]:OFFSETS[1] + SIZE], before)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--uboot', type=Path)
    parser.add_argument('--mkimage', type=Path)
    args, remaining = parser.parse_known_args()
    UBOOT = args.uboot.resolve() if args.uboot else None
    MKIMAGE = args.mkimage.resolve() if args.mkimage else None
    unittest.main(argv=[sys.argv[0], *remaining])
