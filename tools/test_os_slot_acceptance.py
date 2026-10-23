#!/usr/bin/env python3
"""Acceptance evidence must remain attributable and never fabricate a pass."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

MODULE = Path(__file__).parent / "hardware/os_slot_acceptance.py"


class AcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(MODULE.is_file(), "manual hardware acceptance runner is missing")
        spec = importlib.util.spec_from_file_location("os_slot_acceptance", MODULE)
        self.api = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.api)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "image.img").write_bytes(b"abc")
        (self.root / "key").touch()
        (self.root / "known_hosts").write_text("pinned by operator")
        self.config = {"host": "192.0.2.10", "port": 22, "user": "epilykos",
                       "identity_file": str(self.root / "key"), "known_hosts_file": str(self.root / "known_hosts"),
                       "image": str(self.root / "image.img"), "image_sha256": "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
                       "expected_card_cid": "a" * 32, "expected_source": "b" * 40}
        self.snapshot = {"schema": 1, "card_cid": "a" * 32, "model": "Raspberry Pi 3 Model B Rev 1.2",
                         "boot_id": "11111111-1111-4111-8111-111111111111", "cmdline": "rauc.slot=A",
                         "kernel": "6.12.1", "platform": {"source": "b" * 40}, "commands": {}}

    def test_run_binds_image_hash_card_and_source(self):
        run = self.root / "run"
        self.api.initialize(run, self.config)
        identity = json.loads((run / "identity.json").read_bytes())
        self.assertEqual(identity["image_sha256"], self.config["image_sha256"])
        self.assertFalse(identity["hardware_accepted"])
        with self.assertRaises(ValueError):
            self.api.initialize(run, self.config)
        bad = {**self.config, "image_sha256": "0" * 64}
        with self.assertRaises(ValueError):
            self.api.initialize(self.root / "other", bad)
        self.assertFalse((self.root / "other").exists())

    def test_before_after_records_observations_without_manufacturing_a_pass(self):
        run = self.root / "run"
        self.api.initialize(run, self.config)
        self.api.record(run, "install-power-cut", "before", self.snapshot, "installing at 40%")
        after = {**self.snapshot, "boot_id": "22222222-2222-4222-8222-222222222222"}
        self.api.record(run, "install-power-cut", "after", after, "unplugged and reconnected power")
        events = sorted((run / "events").glob("*.json"))
        self.assertEqual(len(events), 2)
        result = json.loads(events[-1].read_bytes())
        self.assertEqual(result["assessment"], "unreviewed")
        self.assertEqual(result["snapshot"]["boot_id"], after["boot_id"])
        self.assertFalse(json.loads((run / "identity.json").read_bytes())["hardware_accepted"])

    def test_wrong_card_wrong_board_and_missing_identity_are_refused(self):
        run = self.root / "run"
        self.api.initialize(run, self.config)
        for changes in [{"card_cid": "c" * 32}, {"card_cid": ""}, {"model": "Raspberry Pi 4"}, {"boot_id": ""}]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.api.record(run, "boot-a", "after", {**self.snapshot, **changes}, "observed")
        self.assertEqual(list((run / "events").glob("*.json")), [])

    def test_after_without_before_and_missing_manual_note_are_refused(self):
        run = self.root / "run"
        self.api.initialize(run, self.config)
        for checkpoint, phase, note in [("install-power-cut", "after", "cut"), ("boot-a", "after", ""),
                                        ("unknown", "before", "note")]:
            with self.assertRaises(ValueError):
                self.api.record(run, checkpoint, phase, self.snapshot, note)

    def test_boot_identity_must_match_the_configured_image_source(self):
        run = self.root / "run"
        self.api.initialize(run, self.config)
        snapshot = {**self.snapshot, "platform": {"source": "c" * 40}}
        with self.assertRaises(ValueError):
            self.api.record(run, "boot-a", "after", snapshot, "boot observed")

    def test_unreachable_device_is_recorded_as_error_not_success(self):
        run = self.root / "run"
        self.api.initialize(run, self.config)
        self.api.record(run, "boot-a", "after", {"error": "SSH connection timed out"}, "router unavailable")
        event = json.loads(next((run / "events").glob("*.json")).read_bytes())
        self.assertEqual(event["assessment"], "collection-failed")

    def test_every_design_case_is_a_checkpoint_and_report_never_passes(self):
        for name in ["healthy-reboots", "bad-dtb", "bad-kernel", "bad-app", "hung-kernel", "supervisor-hang",
                     "good-update", "download-power-cut", "restart-power-cut", "trying-power-cut",
                     "worker-killed-rauc-alive", "refused-packages", "revocation-after-fallback",
                     "app-survives-update", "factory-reset-trying", "measurements", "soak-72h"]:
            self.assertIn(name, self.api.CHECKPOINTS)
        run = self.root / "run"
        self.api.initialize(run, self.config)
        self.api.record(run, "good-update", "before", self.snapshot, "installed from Settings")
        summary = self.api.report(run)
        self.assertEqual(summary["checkpoints"]["good-update"], "partial")
        after = {**self.snapshot, "boot_id": "22222222-2222-4222-8222-222222222222"}
        self.api.record(run, "good-update", "after", after, "restarted, confirmed 0.7")
        self.api.record(run, "boot-b", "after", {"error": "timeout"}, "no network")
        summary = self.api.report(run)
        self.assertEqual(summary["checkpoints"]["good-update"], "observed-unreviewed")
        self.assertEqual(summary["checkpoints"]["boot-b"], "collection-failed")
        self.assertFalse(summary["hardware_accepted"])
        self.assertIn("soak-72h", summary["pending"])
        self.assertNotIn("good-update", summary["pending"])

    def test_collector_reads_the_update_job_without_changing_it(self):
        self.assertIn("view(resume=False)", self.api.COLLECTOR)
        for word in ("epk_os_pending", "epk_os_phase", "guard.json", "os-health.json", "MemoryPeak"):
            self.assertIn(word, self.api.COLLECTOR)
        for verb in ("os_install", "reboot", "mark-active", "fw_setenv", "rm "):
            self.assertNotIn(verb, self.api.COLLECTOR)

    def test_transport_rejects_option_injection_and_requires_pinned_host_file(self):
        for change in [{"host": "-oProxyCommand=bad"}, {"user": "x;bad"}, {"port": True},
                       {"known_hosts_file": str(self.root / "absent")}]:
            with self.assertRaises(ValueError):
                self.api.ssh_arguments({**self.config, **change})
        args = self.api.ssh_arguments(self.config)
        self.assertIn("StrictHostKeyChecking=yes", args)
        self.assertIn("BatchMode=yes", args)
        self.assertIn("IdentitiesOnly=yes", args)
        self.assertIn("UserKnownHostsFile=" + self.config["known_hosts_file"], args)


if __name__ == "__main__":
    unittest.main()
