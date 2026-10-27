#!/usr/bin/env python3
"""tools/make_os_policy.py writes exactly the policy a box accepts, from the
files the key README has the maintainer make, and refuses mismatches."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True
import make_os_policy  # noqa: E402
import os_test_trust  # noqa: E402

TOOLS = Path(__file__).resolve().parent


class MakePolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.dir = Path(cls.temp.name)
        cls.trust = cls.dir / 'trust'
        os_test_trust.generate(cls.trust, 'stable', 'stable-index-1')
        cls.p = cls.trust / 'private'
        cls.args = dict(root_key=cls.p / 'os-index-root.key', root_pub=cls.trust / 'os-index-root.pem',
                        index_key=cls.p / 'stable-index-1.key', key_id='stable-index-1',
                        cas=[cls.trust / 'rauc-ca.pem'], leaves=[cls.trust / 'rauc-signing.pem'])

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_matches_the_policy_the_card_build_makes(self):
        raw, signature, root = make_os_policy.build('stable', **self.args)
        self.assertEqual(raw, (self.trust / 'os-policy.json').read_bytes())
        self.assertEqual(signature, (self.trust / 'os-policy.sig').read_bytes())
        self.assertEqual(root, (self.trust / 'os-index-root.pem').read_bytes())
        self.assertLess(len(raw), 6144)

    def test_command_line_writes_the_three_files_and_never_overwrites(self):
        out = self.dir / 'out'
        command = [sys.executable, str(TOOLS / 'make_os_policy.py'), '--channel', 'stable',
                   '--root-key', str(self.args['root_key']), '--root-pub', str(self.args['root_pub']),
                   '--index-key', str(self.args['index_key']), '--key-id', 'stable-index-1',
                   '--ca', str(self.args['cas'][0]), '--leaf', str(self.args['leaves'][0]), '--output', str(out)]
        done = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(sorted(p.name for p in out.iterdir()), ['os-index-root.pem', 'os-policy.json', 'os-policy.sig'])
        self.assertEqual(json.loads(done.stdout)['trust_epoch'], 1)
        again = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(again.returncode, 1)
        self.assertIn('refusing replacement', again.stderr)

    def test_refuses_mismatched_inputs(self):
        other = self.dir / 'other'
        os_test_trust.generate(other, 'stable', 'stable-index-1')
        cases = {
            'root key is not the root public key': dict(root_key=other / 'private/os-index-root.key'),
            'leaf from a different CA': dict(leaves=[other / 'rauc-signing.pem']),
        }
        for name, change in cases.items():
            with self.subTest(name), self.assertRaises(ValueError):
                make_os_policy.build('stable', **{**self.args, **change})

    def test_epoch_is_carried_into_the_signed_policy(self):
        raw, _, _ = make_os_policy.build('stable', **self.args, trust_epoch=2)
        self.assertEqual(json.loads(raw)['trust_epoch'], 2)


if __name__ == '__main__':
    unittest.main()
