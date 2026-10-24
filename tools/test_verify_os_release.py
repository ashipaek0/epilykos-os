#!/usr/bin/env python3
"""Signed per-package authorization entries (verify_os_release, design §6.2
and §6.4): delegated key, board/channel/protocol/host API, trust epoch,
exact signed bytes, and ordering above the floor and the running slot.
"""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True
import os_trust_fixtures as fx  # noqa: E402
from os_trust_fixtures import trust  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


class EntryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        t = cls.t = fx.Trust(Path(cls.temp.name) / 'keys')
        t.index_key('dev-1')
        t.index_key('outsider')
        t.ca('ca-1')
        t.leaf('leaf-1', 'ca-1')
        cls.policy_pair = t.policy(1, ['dev-1'], ['ca-1'], ['leaf-1'])
        cls.policy = trust.verify_policy(*cls.policy_pair, t.root_pem, board='raspberrypi3', channel='dev')
        cls.platform = fx.platform()

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def refused(self, raw, signature, platform=None):
        with self.assertRaises(trust.TrustError):
            trust.verify_os_release(raw, signature, platform or self.platform, self.policy)

    def test_valid_entry(self):
        entry = trust.verify_os_release(*self.t.entry('dev-1'), self.platform, self.policy)
        self.assertEqual((entry['sequence'], entry['version']), (12, '0.7'))

    def test_key_not_listed_in_the_policy_is_refused(self):
        self.refused(*self.t.entry('outsider'))
        # Claiming a listed key ID while signing with another key.
        self.refused(*self.t.entry('dev-1', sign_with='outsider'))

    def test_any_changed_byte_breaks_the_signature(self):
        raw, signature = self.t.entry('dev-1')
        tampered = raw.replace(b'"sequence":12', b'"sequence":13')
        self.refused(tampered, signature)
        self.refused(raw, signature[:-1] + bytes([signature[-1] ^ 1]))
        self.refused(raw, signature[:63])

    def test_non_canonical_or_unknown_shapes_are_refused(self):
        raw, _ = self.t.entry('dev-1')
        spaced = raw.replace(b',', b', ', 1)
        self.refused(*self.t.entry('dev-1', raw=spaced))
        for override in ({'schema': 2}, {'extra': True}, {'bundle': {'url': 'https://x.invalid/b', 'size': 1}},
                         {'index_key_id': ['dev-1']}):
            with self.subTest(override=override), self.assertRaises(ValueError):
                trust.verify_os_release(*self.t.entry('dev-1', **override), self.platform, self.policy)

    def test_wrong_board_channel_protocol_or_host_api_is_refused(self):
        for override in ({'board': 'raspberrypi4'}, {'channel': 'stable'}, {'boot_protocol': 1},
                         {'min_host_api': 3}):
            with self.subTest(override=override):
                self.refused(*self.t.entry('dev-1', **override))

    def test_entry_must_use_the_current_policy_epoch(self):
        self.refused(*self.t.entry('dev-1', epoch=2))
        self.refused(*self.t.entry('dev-1', epoch=3))
        with self.assertRaises(ValueError):
            trust.verify_os_release(*self.t.entry('dev-1', epoch=0), self.platform, self.policy)

    def test_links_and_bundle_identity_are_bounded(self):
        for bundle in ({'url': 'http://x.invalid/b.raucb', 'size': 1, 'sha256': 'a' * 64},
                       {'url': 'https://x.invalid/b.raucb', 'size': 0, 'sha256': 'a' * 64},
                       {'url': 'https://x.invalid/b.raucb', 'size': 4 * 1024 ** 3, 'sha256': 'a' * 64},
                       {'url': 'https://x.invalid/b.raucb', 'size': 1, 'sha256': 'A' * 64}):
            with self.subTest(bundle=bundle):
                self.refused(*self.t.entry('dev-1', bundle=bundle))
        self.refused(*self.t.entry('dev-1', notes_url='http://x.invalid/notes'))

    def test_replay_and_older_packages_are_not_offered(self):
        entry = trust.verify_os_release(*self.t.entry('dev-1', sequence=12), self.platform, self.policy)
        trust.admissible(entry, self.platform, floor=11)
        with self.assertRaises(trust.TrustError):
            trust.admissible(entry, self.platform, floor=12)              # already confirmed
        with self.assertRaises(trust.TrustError):
            trust.admissible(entry, fx.platform(sequence=12), floor=11)   # running it now
        with self.assertRaises(trust.TrustError):
            trust.admissible(entry, fx.platform(sequence=14), floor=11)   # retained newer slot

    def test_cli_requires_the_signed_entry_and_checks_ordering(self):
        work = Path(self.temp.name) / 'cli'
        work.mkdir()
        files = {'root.pem': self.t.root_pem, 'policy.json': self.policy_pair[0], 'policy.sig': self.policy_pair[1],
                 'platform.json': trust.canonical(self.platform)}
        raw, signature = self.t.entry('dev-1')
        files.update({'entry.json': raw, 'entry.sig': signature})
        for name, data in files.items():
            (work / name).write_bytes(data)
        command = [sys.executable, str(ROOT / 'tools/verify_os_release.py'), '--root', work / 'root.pem',
                   '--policy', work / 'policy.json', '--policy-sig', work / 'policy.sig',
                   '--entry', work / 'entry.json', '--entry-sig', work / 'entry.sig',
                   '--platform', work / 'platform.json']
        ok = subprocess.run([*map(str, command), '--floor', '11'], capture_output=True, text=True)
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertIn('"sequence": 12', ok.stdout)
        old = subprocess.run([*map(str, command), '--floor', '12'], capture_output=True, text=True)
        self.assertEqual(old.returncode, 1)
        self.assertIn('not newer', old.stderr)
        missing = subprocess.run([*map(str, command[:8])], capture_output=True, text=True)
        self.assertNotEqual(missing.returncode, 0)


if __name__ == '__main__':
    unittest.main()
