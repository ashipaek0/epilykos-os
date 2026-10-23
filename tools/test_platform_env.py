#!/usr/bin/env python3
"""Fault tests against native redundant-env bytes and the portable C guard.

Fixtures use Python's independent CRC/serialization; the production parser,
selection and commit engine run in a compiled process, never a state mock.
"""
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest
import zlib

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "os/board/epilykos/raspberrypi3/platform-env"
SIZE = 16384
OFFSETS = (0x400000, 0x404000)


def record(values, flag=1):
    data = b"".join(k.encode() + b"=" + v.encode() + b"\0" for k, v in values.items()) + b"\0"
    if len(data) > SIZE - 5:
        raise ValueError("fixture exceeds native payload")
    data += b"\xff" * (SIZE - 5 - len(data))
    return struct.pack("<I", zlib.crc32(data)) + bytes([flag]) + data


def state(generation="7", floor="12", epoch="2", **extra):
    return {
        "BOOT_ORDER": "A B", "BOOT_A_LEFT": "3", "BOOT_B_LEFT": "3",
        "epk_state_schema": "1", "epk_state_generation": generation,
        "epk_os_floor": floor, "epk_trust_epoch": epoch,
        "epk_trust_policy": "e30=", "epk_trust_policy_sig": "A" * 86 + "==",
        **extra,
    }


def decode(raw):
    if struct.unpack("<I", raw[:4])[0] != zlib.crc32(raw[5:]):
        raise ValueError("bad native CRC")
    return dict(item.split(b"=", 1) for item in raw[5:].split(b"\0\0", 1)[0].split(b"\0"))


class PlatformEnvTests(unittest.TestCase):
    binary = None
    build_dir = None

    @classmethod
    def tearDownClass(cls):
        if cls.build_dir:
            cls.build_dir.cleanup()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.image = Path(self.temp.name) / "card.img"

    def run_native(self, verb, *args, fault=None, ok=True):
        cls = type(self)
        if cls.binary is None:
            self.assertTrue((CORE / "epk_env.c").is_file(), "native generation-aware env guard is missing")
            cls.build_dir = tempfile.TemporaryDirectory()
            binary = Path(cls.build_dir.name) / "env-driver"
            built = subprocess.run([
                "cc", "-std=c99", "-O2", "-Wall", "-Wextra", "-Werror",
                "-I", str(CORE), str(CORE / "epk_env.c"),
                str(ROOT / "tools/platform_env_test_driver.c"), "-o", str(binary),
            ], capture_output=True, text=True)
            self.assertEqual(built.returncode, 0, built.stderr)
            cls.binary = binary
        result = subprocess.run([str(cls.binary), verb, str(self.image), *args],
                                env={**os.environ, **(fault or {})}, capture_output=True, text=True)
        if ok:
            self.assertEqual(result.returncode, 0, result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout)
        return result

    def put(self, a, b):
        with self.image.open("wb") as f:
            f.seek(OFFSETS[0])
            f.write(a)
            f.seek(OFFSETS[1])
            f.write(b)

    def copies(self):
        with self.image.open("rb") as f:
            result = []
            for offset in OFFSETS:
                f.seek(offset)
                result.append(f.read(SIZE))
            return result

    def test_newer_critical_generation_wins_over_native_active_flag(self):
        self.put(record(state(), 250), record(state(generation="8", floor="13", epoch="3"), 1))
        self.assertEqual(self.run_native("select").stdout.strip(), "1 8 13 3")

    def test_same_generation_uses_native_incremental_counter_including_wrap(self):
        for a, b, want in [(5, 6, 1), (255, 0, 1), (0, 255, 0), (5, 5, 0)]:
            with self.subTest(a=a, b=b):
                self.put(record(state(), a), record(state(BOOT_A_LEFT="2"), b))
                self.assertEqual(self.run_native("select").stdout.strip(), f"{want} 7 12 2")

    def test_equal_generation_different_critical_state_is_not_resolved_by_flags(self):
        self.put(record(state(), 1), record(state(floor="13"), 2))
        self.run_native("select", ok=False)

    def test_greater_generation_cannot_lower_floor_or_epoch(self):
        for changes in [dict(floor="11"), dict(epoch="1")]:
            self.put(record(state()), record(state(generation="8", **changes), 2))
            self.run_native("select", ok=False)

    def test_same_epoch_cannot_change_policy_even_at_greater_generation(self):
        self.put(record(state()), record(state(generation="8", epk_trust_policy="W10="), 2))
        self.run_native("select", ok=False)

    def test_unknown_critical_fields_participate_in_equal_generation_comparison(self):
        self.put(record(state(epk_future="one")), record(state(epk_future="two"), 2))
        self.run_native("select", ok=False)

    def test_critical_comparison_is_independent_of_entry_order(self):
        original = state()
        self.put(record(original), record(dict(reversed(list(original.items()))), 2))
        self.assertEqual(self.run_native("select").stdout.strip(), "1 7 12 2")

    def test_crc_valid_undecodable_state_is_not_ignored(self):
        for bad in [dict(generation=""), dict(generation="01"), dict(generation="-1"),
                    dict(generation="18446744073709551616"), dict(epk_state_schema="2")]:
            self.put(record(state()), record(state(**bad), 2))
            self.run_native("select", ok=False)

    def test_duplicate_keys_and_unterminated_data_fail_closed(self):
        raw = record(state())
        payload = raw[5:].replace(b"BOOT_ORDER=A B\0", b"epk_os_floor=0\0", 1)
        duplicate = struct.pack("<I", zlib.crc32(payload)) + b"\x02" + payload
        self.put(raw, duplicate)
        self.run_native("select", ok=False)
        payload = b"x" * (SIZE - 5)
        self.put(raw, struct.pack("<I", zlib.crc32(payload)) + b"\x02" + payload)
        self.run_native("select", ok=False)

    def test_one_bad_crc_is_repairable_both_bad_never_default_to_zero(self):
        good = record(state())
        self.put(b"\0" * SIZE, good)
        self.assertEqual(self.run_native("select").stdout.strip(), "1 7 12 2")
        self.run_native("commit", "BOOT_A_LEFT=2")
        self.assertTrue(all(decode(r)[b"epk_os_floor"] == b"12" for r in self.copies()))
        self.put(b"\0" * SIZE, b"\0" * SIZE)
        before = self.copies()
        self.run_native("commit", "BOOT_A_LEFT=2", ok=False)
        self.assertEqual(self.copies(), before)

    def test_unseeded_legacy_env_is_not_enrolled(self):
        self.put(record({"BOOT_ORDER": "A B"}), record({"BOOT_ORDER": "A B"}, 2))
        self.run_native("select", ok=False)

    def test_critical_commit_increments_generation_and_replicates_both_records(self):
        self.put(record(state(), 1), record(state(), 2))
        self.run_native("commit", "epk_os_floor=13", "epk_trust_epoch=3", "epk_trust_policy=W10=")
        for raw in self.copies():
            values = decode(raw)
            self.assertEqual(values[b"epk_state_generation"], b"8")
            self.assertEqual(values[b"epk_os_floor"], b"13")
            self.assertEqual(values[b"epk_trust_epoch"], b"3")

    def test_acknowledged_policy_survives_newest_copy_loss_and_counter_save(self):
        self.put(record(state(), 1), record(state(), 2))
        self.run_native("commit", "epk_os_floor=13", "epk_trust_epoch=3", "epk_trust_policy=W10=")
        copies = self.copies()
        self.put(copies[0], b"\0" * SIZE)
        self.run_native("commit", "BOOT_A_LEFT=2")
        for raw in self.copies():
            values = decode(raw)
            self.assertEqual(values[b"epk_os_floor"], b"13")
            self.assertEqual(values[b"epk_trust_epoch"], b"3")
            self.assertEqual(values[b"BOOT_A_LEFT"], b"2")

    def test_torn_first_and_second_write_preserve_newest_valid_critical_state(self):
        for cut, want in [("1", "0 7 12 2"), ("2", "1 8 13 2")]:
            with self.subTest(cut=cut):
                self.put(record(state(), 2), record(state(), 1))
                self.run_native("commit", "epk_os_floor=13", fault={"EPK_TEST_TORN_WRITE": cut}, ok=False)
                self.assertEqual(self.run_native("select").stdout.strip(), want)
                self.run_native("commit", "BOOT_A_LEFT=2")
                expected = b"12" if cut == "1" else b"13"
                self.assertTrue(all(decode(r)[b"epk_os_floor"] == expected for r in self.copies()))

    def test_noop_still_forces_replication_and_checks_each_readback(self):
        self.put(record(state(generation="8", floor="13"), 1), record(state(), 2))
        self.run_native("commit")
        self.assertTrue(all(decode(r)[b"epk_os_floor"] == b"13" for r in self.copies()))
        for cut in ("1", "2"):
            self.put(record(state(), 1), record(state(), 2))
            self.run_native("commit", fault={"EPK_TEST_DROP_WRITE": cut}, ok=False)

    def test_commit_cannot_acknowledge_only_one_crc_valid_record(self):
        self.put(record(state(), 2), record(state(), 1))
        self.run_native("commit", "epk_os_floor=13",
                        fault={"EPK_TEST_CORRUPT_EARLIER_COPY": "1"}, ok=False)

    def test_concurrent_writers_reread_under_lock_and_do_not_lose_updates(self):
        self.put(record(state()), record(state(), 2))
        self.run_native("select")  # Build the real executable before spawning.
        workers = [subprocess.Popen([str(type(self).binary), "commit", str(self.image),
                                     f"epk_transaction_{i}=value"],
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

    def test_generation_never_wraps_but_counter_refresh_remains_possible(self):
        maximum = state(generation="18446744073709551615")
        self.put(record(maximum), record(maximum, 2))
        self.run_native("commit", "epk_os_floor=13", ok=False)
        self.run_native("commit", "BOOT_A_LEFT=2")
        self.assertTrue(all(decode(r)[b"epk_state_generation"] == b"18446744073709551615" for r in self.copies()))

    def test_lowering_floors_removing_schema_or_overriding_generation_writes_nothing(self):
        self.put(record(state()), record(state(), 2))
        before = self.copies()
        for update in ["epk_os_floor=11", "epk_trust_epoch=1", "epk_state_schema", "epk_state_generation=9",
                       "epk_trust_policy=W10=", "epk_os_floor=01"]:
            with self.subTest(update=update):
                self.run_native("commit", update, ok=False)
                self.assertEqual(self.copies(), before)

    def test_actual_serialized_budget_reserves_two_kib_in_native_record(self):
        self.put(record(state()), record(state(), 2))
        # Independently derive the exact boundary: entries, '=', both NULs,
        # CRC and native flag all count. One extra byte must be refused.
        baseline_used = sum(len(k) + len(v) + 2 for k, v in state().items()) + 1
        maximum = SIZE - 5 - 2048 - baseline_used - 7
        before = self.copies()
        self.run_native("commit", "extra=" + "x" * (maximum + 1), ok=False)
        self.assertEqual(self.copies(), before)
        self.run_native("commit", "extra=" + "x" * maximum)
        for raw in self.copies():
            used = raw[5:].index(b"\0\0") + 2
            self.assertEqual(SIZE - 5 - used, 2048)

    def test_policy_budget_counts_decoded_bytes_and_base64_expansion(self):
        self.put(record(state()), record(state(), 2))
        self.run_native("commit", "epk_trust_epoch=3", "epk_trust_policy=" + "A" * 8192)
        self.assertTrue(all(len(decode(r)[b"epk_trust_policy"]) == 8192 for r in self.copies()))
        self.run_native("commit", "epk_trust_epoch=4", "epk_trust_policy=" + "A" * 8196, ok=False)

    def test_malformed_base64_and_wrong_signature_size_are_refused(self):
        for value in ["", "e3=", "e3==", "e3$=", "e30===", "e30=AAAA"]:
            self.put(record(state()), record(state(epk_trust_policy=value), 2))
            self.run_native("select", ok=False)
        self.put(record(state()), record(state(epk_trust_policy_sig="AA=="), 2))
        self.run_native("select", ok=False)

    def test_native_snapshot_diff_advances_generation_and_rejects_stale_export(self):
        self.put(record(state()), record(state(), 2))
        desired = Path(self.temp.name) / "desired.env"
        desired.write_bytes(record(state(floor="13", BOOT_A_LEFT="2")))
        self.run_native("commit-snapshot", str(desired))
        for raw in self.copies():
            values = decode(raw)
            self.assertEqual(values[b"epk_state_generation"], b"8")
            self.assertEqual(values[b"epk_os_floor"], b"13")
            self.assertEqual(values[b"BOOT_A_LEFT"], b"2")
        before = self.copies()
        desired.write_bytes(record(state(floor="14")))  # Still generation 7.
        self.run_native("commit-snapshot", str(desired), ok=False)
        self.assertEqual(self.copies(), before)

    def test_native_snapshot_deletions_and_diff_limit_are_atomic(self):
        self.put(record(state(epk_pending="old")), record(state(epk_pending="old"), 2))
        desired = Path(self.temp.name) / "desired.env"
        desired.write_bytes(record(state()))
        self.run_native("commit-snapshot", str(desired))
        self.assertTrue(all(b"epk_pending" not in decode(r) for r in self.copies()))
        before = self.copies()
        desired.write_bytes(record(state(generation="8", **{f"item_{i}": "v" for i in range(65)})))
        self.run_native("commit-snapshot", str(desired), ok=False)
        self.assertEqual(self.copies(), before)


if __name__ == "__main__":
    unittest.main()
