#!/usr/bin/env python3
"""Real crypto/native-record tests for first-flash bootstrap artifacts."""
import argparse
import base64
import sys
import copy
import importlib.util
import json
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest
import zlib

ROOT = Path(__file__).resolve().parents[1]
GENERATED_DEFAULTS = None


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


class BootstrapTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.work = Path(cls.temp.name)
        module = ROOT / "tools/os_bootstrap.py"
        assert module.is_file(), "authenticated bootstrap generator is missing"
        spec = importlib.util.spec_from_file_location("os_bootstrap", module)
        cls.api = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.api)
        for name in ["root", "other", "index"]:
            subprocess.run(["openssl", "genpkey", "-algorithm", "ED25519", "-out",
                            str(cls.work / f"{name}.key")], check=True, capture_output=True)
            subprocess.run(["openssl", "pkey", "-in", str(cls.work / f"{name}.key"),
                            "-pubout", "-out", str(cls.work / f"{name}.pub")],
                           check=True, capture_output=True)
        subprocess.run(["openssl", "req", "-new", "-x509", "-key", str(cls.work / "other.key"),
                        "-out", str(cls.work / "ca.pem"), "-days", "2", "-subj", "/CN=test-only",
                        "-addext", "basicConstraints=critical,CA:TRUE"], check=True, capture_output=True)
        der = subprocess.run(["openssl", "pkey", "-pubin", "-in", str(cls.work / "index.pub"),
                              "-outform", "DER"], check=True, capture_output=True).stdout
        cls.policy = {"schema": 1, "board": "raspberrypi3", "channel": "dev", "trust_epoch": 2,
                      "index_keys": [{"key_id": "dev-1", "public_key": base64.b64encode(der[-32:]).decode()}],
                      "rauc_cas": [(cls.work / "ca.pem").read_text()],
                      "rauc_leaf_spki_sha256": ["a" * 64]}
        cls.platform = {"schema": 1, "board": "raspberrypi3", "arch": "arm64",
                        "slot_layout": "pi3-mbr-v1", "boot_protocol": 2, "channel": "dev",
                        "os_version": "0.7.0-dev.1", "os_sequence": 17,
                        "source": "b" * 40, "kernel": "6.12.1", "host_api": 2,
                        "app_release": "3.0.0-dev.1"}
        cls.driver = cls.work / "env-driver"
        core = ROOT / "os/board/epilykos/raspberrypi3/platform-env"
        subprocess.run(["cc", "-std=c99", "-O2", "-Wall", "-Wextra", "-Werror", "-I", str(core),
                        str(core / "epk_env.c"), str(ROOT / "tools/platform_env_test_driver.c"),
                        "-o", str(cls.driver)], check=True, capture_output=True)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def sign(self, raw):
        with tempfile.TemporaryDirectory(dir=self.work) as directory:
            path = Path(directory) / "policy"
            path.write_bytes(raw)
            return subprocess.run(["openssl", "pkeyutl", "-sign", "-rawin", "-inkey",
                                   str(self.work / "root.key"), "-in", str(path)],
                                  check=True, capture_output=True).stdout

    def verify(self, policy=None, raw=None, sig=None, root="root", **kwargs):
        raw = raw if raw is not None else canonical(policy if policy is not None else self.policy)
        sig = sig if sig is not None else self.sign(raw)
        return self.api.verify_policy(raw, sig, (self.work / f"{root}.pub").read_bytes(),
                                      board="raspberrypi3", channel="dev", **kwargs)

    def seed(self, defaults=b"bootcmd=bootflow scan\nbootdelay=2\n", policy=None, platform=None):
        raw = canonical(policy if policy is not None else self.policy)
        return self.api.build_seed(defaults, platform or self.platform, raw, self.sign(raw),
                                   (self.work / "root.pub").read_bytes())

    def native(self, pair):
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / "card.img"
            with image.open("wb") as stream:
                for offset, data in zip([0x400000, 0x404000], pair):
                    stream.seek(offset)
                    stream.write(data)
            result = subprocess.run([str(self.driver), "select", str(image)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            return result.stdout.strip()

    def test_artifacts_are_complete_and_existing_output_is_never_replaced(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "seed"
            args = (b"bootcmd=bootflow scan\n", self.platform, canonical(self.policy),
                    self.sign(canonical(self.policy)), (self.work / "root.pub").read_bytes())
            self.api.write_artifacts(output, *args)
            self.assertEqual(self.native(((output / "env-a.bin").read_bytes(),
                                          (output / "env-b.bin").read_bytes())), "1 0 17 2")
            before = {p.name: p.read_bytes() for p in output.iterdir()}
            with self.assertRaises(ValueError):
                self.api.write_artifacts(output, *args)
            self.assertEqual(before, {p.name: p.read_bytes() for p in output.iterdir()})
            empty = Path(directory) / "empty"
            empty.mkdir()
            with self.assertRaises(ValueError):
                self.api.write_artifacts(empty, *args)
            self.assertEqual(list(empty.iterdir()), [])
            link = Path(directory) / "link"
            link.symlink_to(empty)
            with self.assertRaises(ValueError):
                self.api.write_artifacts(link, *args)

    def test_actual_generated_defaults_have_native_seed_and_runtime_headroom(self):
        if GENERATED_DEFAULTS is None:
            self.skipTest("actual U-Boot defaults exercised by test-native-env")
        a, b, budget = self.seed(GENERATED_DEFAULTS.read_bytes())
        self.assertEqual(self.native((a, b)), "1 0 17 2")
        self.assertGreaterEqual(budget["worst_case_free_bytes"], 2048)
        print("\nGenerated U-Boot defaults budget: " + json.dumps(budget, sort_keys=True))

    def test_real_root_signature_is_required(self):
        self.assertEqual(self.verify()["trust_epoch"], 2)
        with self.assertRaises(ValueError):
            self.verify(root="other")
        with self.assertRaises(ValueError):
            self.verify(sig=b"\0" * 64)
        with self.assertRaises(ValueError):
            self.verify(sig=b"\0" * 63)

    def test_public_root_input_cannot_carry_a_private_key_suffix(self):
        raw = canonical(self.policy)
        mixed = (self.work / "root.pub").read_bytes() + (self.work / "root.key").read_bytes()
        with self.assertRaises(ValueError):
            self.api.verify_policy(raw, self.sign(raw), mixed, board="raspberrypi3", channel="dev")

    def test_default_entry_budget_reserves_future_transaction_entries(self):
        defaults = b"".join(f"x{i}=a\n".encode() for i in range(200))
        with self.assertRaises(ValueError):
            self.seed(defaults)

    def test_policy_must_be_canonical_and_unambiguous(self):
        raw = canonical(self.policy)
        for bad in [raw + b"\n", b'{"schema":1,' + raw[1:], raw.replace(b'"schema":1', b'"schema":true'),
                    b"x" * 6145]:
            with self.subTest(raw=bad[:30]), self.assertRaises(ValueError):
                self.verify(raw=bad)

    def test_policy_scope_and_key_material_are_checked(self):
        cases = [("channel", "stable"), ("board", "raspberrypi4"), ("trust_epoch", 0),
                 ("trust_epoch", True), ("index_keys", []), ("rauc_cas", []),
                 ("rauc_leaf_spki_sha256", ["A" * 64]), ("future", "not understood"),
                 ("index_keys", [{"key_id": "dev-1", "public_key": "AA=="}])]
        for name, value in cases:
            policy = {**self.policy, name: value}
            with self.subTest(name=name, value=value), self.assertRaises(ValueError):
                self.verify(policy)
        policy = copy.deepcopy(self.policy)
        policy["index_keys"] *= 2
        with self.assertRaises(ValueError):
            self.verify(policy)
        policy["index_keys"] = self.policy["index_keys"]
        policy["rauc_cas"] = ["not a certificate"]
        with self.assertRaises(ValueError):
            self.verify(policy)

    def test_prior_policy_epoch_never_rolls_back_or_changes_in_place(self):
        old = {"raw": canonical(self.policy), "trust_epoch": 2}
        self.assertEqual(self.verify(current=old)["trust_epoch"], 2)
        for epoch in [1, 2]:
            modified = {**self.policy, "trust_epoch": epoch, "rauc_leaf_spki_sha256": ["c" * 64]}
            with self.assertRaises(ValueError):
                self.verify(modified, current=old)
        self.assertEqual(self.verify({**self.policy, "trust_epoch": 3}, current=old)["trust_epoch"], 3)

    def test_seed_records_pass_native_selection_with_explicit_floor(self):
        a, b, budget = self.seed()
        self.assertEqual(len(a), 16384)
        self.assertEqual(len(b), 16384)
        self.assertEqual(a[5:], b[5:])
        self.assertEqual(self.native((a, b)), "1 0 17 2")
        for raw in [a, b]:
            self.assertEqual(struct.unpack("<I", raw[:4])[0], zlib.crc32(raw[5:]))
        self.assertGreaterEqual(budget["worst_case_free_bytes"], 2048)
        self.assertEqual(budget["runtime_reserve_bytes"], 4096)
        self.assertEqual(budget["policy_max_decoded_bytes"], 6144)

    def test_generated_budget_includes_defaults_maximum_policy_and_future_state(self):
        a, b, report = self.seed(b"bootcmd=bootflow scan\nextra=" + b"x" * 1000 + b"\n")
        self.native((a, b))
        self.assertGreater(report["worst_case_used_bytes"], 1000 + 8192 + 4096)
        with self.assertRaises(ValueError):
            self.seed(b"extra=" + b"x" * 4000 + b"\n")

    def test_defaults_cannot_smuggle_platform_state_or_ambiguous_entries(self):
        for data in [b"epk_os_floor=0\n", b"BOOT_ORDER=B A\n", b"x=a\nx=b\n", b"x=a\0b\n",
                     b"x=a\n\n", b"bad name=x\n", b"x=\xff\n"]:
            with self.subTest(data=data), self.assertRaises(ValueError):
                self.seed(data)

    def test_platform_identity_and_sequence_are_strict(self):
        for key, value in [("boot_protocol", 1), ("os_sequence", None), ("os_sequence", True),
                           ("os_sequence", 0), ("os_sequence", -1), ("source", "unknown"),
                           ("channel", "stable"), ("board", "raspberrypi4")]:
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                self.seed(platform={**self.platform, key: value})


class SourceStateTests(unittest.TestCase):
    def setUp(self):
        module = ROOT / "tools/bootstrap_export.py"
        self.assertTrue(module.is_file(), "strict source export/adoption planner is missing")
        spec = importlib.util.spec_from_file_location("bootstrap_export", module)
        self.api = importlib.util.module_from_spec(spec)
        # Ordinary command execution can import its sibling module.
        sys.path.insert(0, str(ROOT / "tools"))
        self.addCleanup(sys.path.remove, str(ROOT / "tools"))
        spec.loader.exec_module(self.api)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.image_env = ("EPILYKOS_CHANNEL=dev\nEPILYKOS_RELEASE=3.0.0-dev.1\n"
                          "EPILYKOS_SEQUENCE=10\nEPILYKOS_IMAGE_REF=docker.io/ashipaek0/epilykos@sha256:" + "a" * 64 +
                          "\nEPILYKOS_IMAGE_ID=" + "b" * 64 + "\nEPILYKOS_OS_VERSION=0.6-dev+abc1234\n")
        path = self.root / "usr/lib/epilykos"
        path.mkdir(parents=True)
        (path / "image.env").write_text(self.image_env)
        self.updater = self.root / "data/epilykos/updater"
        self.updater.mkdir(parents=True)
        (self.root / "data/epilykos/app-data").mkdir()
        self.promoted = {"release": "3.0.0-dev.2", "channel": "dev", "sequence": 12,
                         "digest": "sha256:" + "c" * 64, "image_id": "d" * 64,
                         "source": "ashipaek0/epilykos"}

    def source(self):
        return self.api.collect_source(self.root, allow_legacy=True)

    def promote(self):
        (self.updater / "current.json").write_text(json.dumps(self.promoted))
        (self.updater / "floor").write_text("12\n")
        (self.updater / "active.env").write_text("EPILYKOS_ACTIVE_IMAGE_ID=" + "d" * 64 +
            "\nEPILYKOS_ACTIVE_SEQUENCE=12\nEPILYKOS_ACTIVE_RELEASE=3.0.0-dev.2\n")

    def test_pristine_legacy_source_is_explicit_and_does_not_invent_os_floor(self):
        source = self.source()
        self.assertEqual(source["app_floor"], 10)
        self.assertEqual(source["app"]["image_id"], "b" * 64)
        self.assertIsNone(source["os_floor"])
        self.assertIsNone(source["trust_epoch"])
        with self.assertRaises(ValueError):
            self.api.collect_source(self.root)

    def test_promoted_floor_and_identity_are_exported_strictly(self):
        self.promote()
        source = self.source()
        self.assertEqual(source["app_floor"], 12)
        self.assertEqual(source["app"]["digest"], "sha256:" + "c" * 64)

    def test_missing_or_malformed_promoted_floor_is_never_default_zero(self):
        self.promote()
        (self.updater / "floor").unlink()
        with self.assertRaises(ValueError):
            self.source()
        for floor in ["", "garbage", "0", "11", "13", "012", "-1"]:
            (self.updater / "floor").write_text(floor)
            with self.subTest(floor=floor), self.assertRaises(ValueError):
                self.source()

    def test_in_progress_or_inconsistent_app_state_cannot_be_exported(self):
        self.promote()
        (self.updater / "job.json").write_text('{}')
        with self.assertRaises(ValueError):
            self.source()
        (self.updater / "job.json").unlink()
        (self.updater / "active.env").write_text("EPILYKOS_ACTIVE_IMAGE_ID=" + "f" * 64 +
            "\nEPILYKOS_ACTIVE_SEQUENCE=12\nEPILYKOS_ACTIVE_RELEASE=3.0.0-dev.2\n")
        with self.assertRaises(ValueError):
            self.source()

    def test_lost_protocol2_metadata_is_not_treated_as_legacy(self):
        (self.root / "usr/lib/epilykos/image.env").write_text(self.image_env.replace("0.6-dev+abc1234", "0.7.0"))
        with self.assertRaises(ValueError):
            self.source()
        (self.root / "usr/lib/epilykos/image.env").write_text(self.image_env)
        (self.root / "usr/lib/epilykos/platform.json").write_text('{"boot_protocol":2}')
        with self.assertRaises(ValueError):
            self.source()

    def test_same_channel_new_baseline_must_meet_existing_floor(self):
        self.promote()
        source = self.source()
        target = self.api.parse_baseline(self.image_env.encode())
        with self.assertRaises(ValueError):
            self.api.adoption_plan(source, target)
        target["sequence"] = 13
        plan = self.api.adoption_plan(source, target)
        self.assertEqual(plan["app_floor"], 12)
        self.assertEqual(plan["target_app"]["sequence"], 13)
        self.assertFalse(plan["installed"])
        with self.assertRaises(ValueError):
            self.api.adoption_plan(source, {**target, "channel": "stable"})
        with self.assertRaises(ValueError):
            self.api.adoption_plan({"database_backup": "untrusted"}, target)

    def test_export_lease_excludes_real_app_maintenance_lock(self):
        import fcntl
        lock = self.root / "data/epilykos/app-data/.maintenance.lock"
        with lock.open("a+") as held:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(ValueError):
                with self.api.source_lease(self.root):
                    self.fail("concurrent source export acquired app maintenance lock")
        with self.api.source_lease(self.root):
            self.assertEqual(self.source()["app_floor"], 10)

    def test_server_holds_source_lease_until_client_receipt_acknowledgement(self):
        import fcntl
        nonce = "a" * 64
        code = ("from pathlib import Path;import bootstrap_export as e;"
                f"e.serve({nonce!r},True,root=Path({str(self.root)!r}))")
        process = subprocess.Popen([sys.executable, "-c", code], cwd=ROOT / "tools",
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            response = self.api.receive_line(process)
            self.assertEqual(response["nonce"], nonce)
            self.assertEqual(response["source"]["app_floor"], 10)
            with self.assertRaises(ValueError):
                with self.api.source_lease(self.root):
                    self.fail("export released maintenance before receipt acknowledgement")
            process.stdin.write((nonce + "\n").encode())
            process.stdin.flush()
            self.assertEqual(process.wait(timeout=5), 0, process.stderr.read().decode())
            with self.api.source_lease(self.root):
                pass
        finally:
            if process.poll() is None:
                process.kill()
            process.wait()
            for stream in [process.stdin, process.stdout, process.stderr]:
                stream.close()

    def test_partial_acknowledgement_times_out_and_releases_maintenance(self):
        nonce = "a" * 64
        code = ("from pathlib import Path;import bootstrap_export as e;e.ACK_TIMEOUT=0.1;"
                f"e.serve({nonce!r},True,root=Path({str(self.root)!r}))")
        process = subprocess.Popen([sys.executable, "-c", code], cwd=ROOT / "tools",
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            self.assertEqual(self.api.receive_line(process)["nonce"], nonce)
            process.stdin.write(b"a")  # No newline; pipe deliberately stays open.
            process.stdin.flush()
            self.assertNotEqual(process.wait(timeout=2), 0)
            with self.api.source_lease(self.root):
                pass
        finally:
            if process.poll() is None:
                process.kill()
            process.wait()
            for stream in [process.stdin, process.stdout, process.stderr]:
                stream.close()

    def test_symlink_state_and_duplicate_metadata_are_refused(self):
        outside = self.root / "outside"
        outside.write_text("10")
        (self.updater / "floor").symlink_to(outside)
        with self.assertRaises(ValueError):
            self.source()
        (self.updater / "floor").unlink()
        (self.root / "usr/lib/epilykos/image.env").write_text(self.image_env + "EPILYKOS_SEQUENCE=1\n")
        with self.assertRaises(ValueError):
            self.source()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--defaults", type=Path)
    args, remaining = parser.parse_known_args()
    GENERATED_DEFAULTS = args.defaults
    unittest.main(argv=[sys.argv[0], *remaining])
