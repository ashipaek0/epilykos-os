#!/usr/bin/env python3
"""Integration tests against built upstream fw-env executables, not a mock."""
import argparse
import os
import shlex
from pathlib import Path
import subprocess
import tempfile
import unittest

from test_platform_env import OFFSETS, SIZE, decode, record, state

FW_TOOLS = None
FAULT_LIBRARY = None
RAUC = None


class NativeEnvTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.card = self.root / "card.img"
        self.config = self.root / "fw_env.config"
        self.config.write_text("".join(f"{self.card} {offset:#x} {SIZE:#x}\n" for offset in OFFSETS))
        self.setter = self.root / "fw_setenv"
        self.setter.symlink_to(FW_TOOLS)

    def put(self, a, b):
        with self.card.open("wb") as f:
            for offset, raw in zip(OFFSETS, (a, b)):
                f.seek(offset)
                f.write(raw)

    def call(self, *args, write=False, ok=True, fault=None):
        env = os.environ.copy()
        if fault:
            env.update(fault)
            env["LD_PRELOAD"] = str(FAULT_LIBRARY)
        result = subprocess.run([str(self.setter if write else FW_TOOLS), "-c", str(self.config),
                                 "-l", str(self.root), *args], env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode == 0, ok, result.stdout + result.stderr)
        return result.stdout.strip()

    def copies(self):
        with self.card.open("rb") as f:
            result = []
            for offset in OFFSETS:
                f.seek(offset)
                result.append(f.read(SIZE))
            return result

    def test_native_reader_prefers_critical_generation_over_active_flag(self):
        self.put(record(state(), 2), record(state(generation="8", floor="13"), 1))
        self.assertEqual(self.call("-n", "epk_os_floor"), "13")

    def test_native_reader_refuses_conflicting_and_missing_platform_state(self):
        for a, b in [(record(state()), record(state(floor="13"), 2)),
                     (record(state()), record(state(generation="8", floor="11"), 2)),
                     (record(state()), record(state(epk_state_schema="2"), 2)),
                     (b"\0" * SIZE, b"\0" * SIZE),
                     (record({"BOOT_A_LEFT": "3"}), record({"BOOT_A_LEFT": "3"}, 2))]:
            self.put(a, b)
            self.call("-n", "BOOT_A_LEFT", ok=False)
            self.call("BOOT_A_LEFT", "3", write=True, ok=False)
            self.assertEqual(self.copies(), [a, b])

    def test_native_counter_write_repairs_newer_generation_without_rollback(self):
        self.put(record(state(), 2), record(state(generation="8", floor="13"), 1))
        self.call("BOOT_A_LEFT", "2", write=True)
        for raw in self.copies():
            values = decode(raw)
            self.assertEqual(values[b"epk_state_generation"], b"8")
            self.assertEqual(values[b"epk_os_floor"], b"13")
            self.assertEqual(values[b"BOOT_A_LEFT"], b"2")

    def test_noop_native_counter_refresh_forces_both_copies(self):
        self.put(record(state(), 2), b"\0" * SIZE)
        self.call("BOOT_A_LEFT", "3", write=True)
        self.assertTrue(all(decode(r)[b"epk_os_floor"] == b"12" for r in self.copies()))
        self.call("BOOT_A_LEFT", "3", write=True,
                  fault={"EPK_TEST_DROP_WRITE": "1"}, ok=False)

    def test_batch_critical_update_increments_generation_once_and_preserves_all_fields(self):
        self.put(record(state(), 1), record(state(), 2))
        batch = self.root / "batch.txt"
        batch.write_text("epk_os_floor 13\nepk_trust_epoch 3\nepk_trust_policy W10=\nBOOT_ORDER B A\n")
        self.call("-s", str(batch), write=True)
        for raw in self.copies():
            values = decode(raw)
            self.assertEqual(values[b"epk_state_generation"], b"8")
            self.assertEqual(values[b"epk_os_floor"], b"13")
            self.assertEqual(values[b"epk_trust_epoch"], b"3")
            self.assertEqual(values[b"BOOT_ORDER"], b"B A")

    def test_invalid_or_truncated_batch_never_commits_a_successful_prefix(self):
        for suffix in ["epk_state_generation 9\n", "epk_os_floor 01\n", "unfinished value"]:
            self.put(record(state(), 1), record(state(), 2))
            before = self.copies()
            batch = self.root / "batch.txt"
            batch.write_text("BOOT_ORDER B A\n" + suffix)
            self.call("-s", str(batch), write=True, ok=False)
            self.assertEqual(self.copies(), before)

    def test_refused_single_write_is_not_reported_as_success(self):
        self.put(record(state(), 1), record(state(), 2))
        before = self.copies()
        for name, value in [("epk_state_generation", "8"), ("epk_os_floor", "11"),
                            ("epk_state_schema", "2")]:
            self.call(name, value, write=True, ok=False)
            self.assertEqual(self.copies(), before)

    def test_real_native_torn_first_and_second_writes_keep_newest_valid_state(self):
        for cut, want in [("1", "12"), ("2", "13")]:
            self.put(record(state(), 2), record(state(), 1))
            self.call("epk_os_floor", "13", write=True, ok=False,
                      fault={"EPK_TEST_TORN_WRITE": cut})
            self.assertEqual(self.call("-n", "epk_os_floor"), want)
            self.call("BOOT_A_LEFT", "2", write=True)
            self.assertTrue(all(decode(r)[b"epk_os_floor"] == want.encode() for r in self.copies()))

    def test_failed_fsync_and_lying_write_cannot_acknowledge_a_commit(self):
        for variable in ["EPK_TEST_FAIL_FSYNC", "EPK_TEST_DROP_WRITE"]:
            for cut in ["1", "2"]:
                self.put(record(state(), 2), record(state(), 1))
                self.call("epk_os_floor", "13", write=True, ok=False, fault={variable: cut})

    def test_actual_native_cli_lock_covers_read_selection_and_full_batch(self):
        self.put(record(state()), record(state(), 2))
        workers = [subprocess.Popen([str(self.setter), "-c", str(self.config), "-l", str(self.root),
                                     f"epk_transaction_{i}", "value"],
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                   for i in range(8)]
        for worker in workers:
            out, error = worker.communicate(timeout=10)
            self.assertEqual(worker.returncode, 0, out + error)
        for raw in self.copies():
            values = decode(raw)
            self.assertEqual(values[b"epk_state_generation"], b"15")
            for i in range(8):
                self.assertEqual(values[f"epk_transaction_{i}".encode()], b"value")

    def test_layout_mismatch_cannot_use_legacy_fallback(self):
        self.put(record(state()), record(state(), 2))
        self.config.write_text(f"{self.card} {OFFSETS[0]:#x} {SIZE:#x}\n")
        self.call("-n", "epk_os_floor", ok=False)
        self.call("BOOT_A_LEFT", "2", write=True, ok=False)

    def rauc(self, action, slot, ok=True):
        directory = self.root / "bin"
        directory.mkdir(exist_ok=True)
        for name, executable in [("fw_printenv", FW_TOOLS), ("fw_setenv", self.setter)]:
            wrapper = directory / name
            wrapper.write_text("#!/bin/sh\nexec " + shlex.quote(str(executable)) +
                               " -c " + shlex.quote(str(self.config)) + " -l " +
                               shlex.quote(str(self.root)) + ' "$@"\n')
            wrapper.chmod(0o755)
        configuration = self.root / "system.conf"
        configuration.write_text(
            "[system]\ncompatible=epilykos-pi3-test\nbootloader=uboot\n"
            f"statusfile={self.root}/status.raucs\nboot-attempts=3\nboot-attempts-primary=3\n"
            f"[slot.rootfs.0]\ndevice={self.root}/root-a\ntype=ext4\nbootname=A\n"
            f"[slot.rootfs.1]\ndevice={self.root}/root-b\ntype=ext4\nbootname=B\n")
        (self.root / "root-a").touch()
        (self.root / "root-b").touch()
        result = subprocess.run([str(RAUC), "--conf", str(configuration), "status",
                                 "--override-boot-slot=A", action, slot],
                                env={**os.environ, "PATH": str(directory) + os.pathsep + os.environ["PATH"]},
                                capture_output=True, text=True)
        self.assertEqual(result.returncode == 0, ok, result.stdout + result.stderr)

    def test_real_rauc_mark_good_and_mark_active_use_guarded_native_writers(self):
        self.put(record(state(), 2), record(state(generation="8", floor="13", BOOT_A_LEFT="1"), 1))
        self.rauc("mark-good", "booted")
        for raw in self.copies():
            values = decode(raw)
            self.assertEqual(values[b"BOOT_A_LEFT"], b"3")
            self.assertEqual(values[b"epk_os_floor"], b"13")
            self.assertEqual(values[b"epk_state_generation"], b"8")
        self.rauc("mark-active", "rootfs.1")
        for raw in self.copies():
            values = decode(raw)
            self.assertEqual(values[b"BOOT_ORDER"], b"B A")
            self.assertEqual(values[b"BOOT_B_LEFT"], b"3")
            self.assertEqual(values[b"epk_os_floor"], b"13")
            self.assertEqual(values[b"epk_state_generation"], b"8")

    def test_real_rauc_refuses_ambiguous_env_instead_of_overwriting_it(self):
        self.put(record(state()), record(state(floor="13"), 2))
        before = self.copies()
        self.rauc("mark-good", "booted", ok=False)
        self.rauc("mark-active", "rootfs.1", ok=False)
        self.assertEqual(self.copies(), before)

    def test_acknowledged_revocation_survives_copy_loss_and_real_rauc_refresh(self):
        self.put(record(state(), 1), record(state(), 2))
        batch = self.root / "policy.txt"
        batch.write_text("epk_os_floor 13\nepk_trust_epoch 3\nepk_trust_policy W10=\n")
        self.call("-s", str(batch), write=True)
        a, _ = self.copies()
        self.put(a, b"\0" * SIZE)  # B was the last-written native copy.
        self.rauc("mark-good", "booted")
        for raw in self.copies():
            values = decode(raw)
            self.assertEqual(values[b"epk_state_generation"], b"8")
            self.assertEqual(values[b"epk_os_floor"], b"13")
            self.assertEqual(values[b"epk_trust_epoch"], b"3")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--fw-tools", type=Path, required=True)
    parser.add_argument("--fault-library", type=Path, required=True)
    parser.add_argument("--rauc", type=Path, required=True)
    args, remaining = parser.parse_known_args()
    FW_TOOLS = args.fw_tools.resolve()
    FAULT_LIBRARY = args.fault_library.resolve()
    RAUC = args.rauc.resolve()
    unittest.main(argv=[__file__, *remaining])
