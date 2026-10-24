#!/usr/bin/env python3
"""Exercise the publication policy actually consumed by the OS workflow."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).with_name('image_release_policy.py')


class ReleaseGates(unittest.TestCase):
    def run_policy(self, **overrides):
        context = dict(validation='success', build='success', artifact='true',
                       channel='stable', event='push', ref='refs/heads/main',
                       new_version='true', source_on_main='true', image_from_run='',
                       image_sha256='ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad')
        context.update(overrides)
        with tempfile.TemporaryDirectory() as d:
            output = Path(d) / 'output'
            result = subprocess.run([sys.executable, str(SCRIPT)], text=True,
                                    capture_output=True,
                                    env=dict(os.environ, GITHUB_OUTPUT=str(output),
                                             **{'POLICY_' + k.upper(): v for k, v in context.items()}))
            self.assertEqual(result.returncode, 0, result.stderr)
            decision = json.loads(result.stdout)
            outputs = dict(line.split('=', 1) for line in output.read_text().splitlines())
            self.assertEqual(outputs['publish'], str(decision['publish']).lower())
            self.assertEqual(outputs['diagnose'], str(decision['diagnose']).lower())
            return decision

    def test_main_release_is_automatic_without_qemu_or_manual_approval(self):
        result = self.run_policy()
        self.assertTrue(result['publish'])
        self.assertTrue(result['diagnose'])

    def test_each_failed_or_missing_prerequisite_blocks_publication(self):
        for values in ({'validation': 'failure'}, {'validation': 'skipped'},
                       {'build': 'failure'}, {'build': 'cancelled'},
                       {'artifact': ''}, {'artifact': 'false'},
                       {'image_sha256': ''}, {'image_sha256': 'bad'},
                       {'channel': 'dev'}, {'source_on_main': 'false'},
                       {'event': 'pull_request'}, {'ref': 'refs/heads/dev'},
                       {'new_version': 'false'}, {'image_from_run': '123'}):
            with self.subTest(values=values):
                self.assertFalse(self.run_policy(**values)['publish'])

    def test_protocol2_cards_are_never_published_or_qemu_booted(self):
        for overrides in ({}, {'event': 'release', 'ref': 'refs/tags/v1.2'}):
            with self.subTest(overrides=overrides):
                result = self.run_policy(boot_protocol='2', **overrides)
                self.assertFalse(result['publish'])
                self.assertFalse(result['diagnose'])
                self.assertIn('protocol_1_image', result['blocking'])
        self.assertTrue(self.run_policy(boot_protocol='1')['publish'])
        self.assertTrue(self.run_policy(boot_protocol='')['publish'], 'older workflow runs stay protocol 1')

    def test_no_image_means_no_boot_attempt(self):
        for build in ('failure', 'cancelled', 'skipped'):
            self.assertFalse(self.run_policy(build=build, artifact='')['diagnose'])

    def test_retained_image_with_structural_failure_is_diagnostic_only(self):
        decision = self.run_policy(build='failure', artifact='true')
        self.assertTrue(decision['diagnose'])
        self.assertFalse(decision['publish'])

    def test_explicit_previous_image_retest_never_publishes(self):
        decision = self.run_policy(build='skipped', artifact='', image_from_run='39036230265')
        self.assertTrue(decision['diagnose'])
        self.assertFalse(decision['publish'])
        for invalid in ('-1', '0', 'abc', '123;echo bad'):
            self.assertFalse(self.run_policy(build='skipped', artifact='',
                                             image_from_run=invalid)['diagnose'])

    def test_stable_manual_dispatch_requires_main_source_and_channel(self):
        self.assertTrue(self.run_policy(event='workflow_dispatch')['publish'])
        self.assertFalse(self.run_policy(event='workflow_dispatch', ref='refs/heads/dev')['publish'])
        self.assertFalse(self.run_policy(event='workflow_dispatch', channel='dev')['publish'])

    def test_published_release_requires_valid_os_tag_and_main_source(self):
        self.assertTrue(self.run_policy(event='release', ref='refs/tags/v1.0',
                                        new_version='false')['publish'])
        for ref in ('refs/heads/main', 'refs/tags/app-dev', 'refs/tags/v1.0.0'):
            self.assertFalse(self.run_policy(event='release', ref=ref)['publish'])
        self.assertFalse(self.run_policy(event='release', ref='refs/tags/v1.0',
                                         source_on_main='false')['publish'])

    def test_publish_verifies_actual_artifact_bytes(self):
        expected = 'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad'
        with tempfile.TemporaryDirectory() as d:
            image = Path(d) / 'image.zst'
            image.write_bytes(b'abc')
            command = [sys.executable, str(SCRIPT), '--verify-artifact', str(image),
                       '--sha256', expected]
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(json.loads(result.stdout)['artifact_verified'])
            for data in (b'abd', b''):
                image.write_bytes(data)
                self.assertNotEqual(subprocess.run(command, capture_output=True).returncode, 0)
            image.unlink()
            self.assertNotEqual(subprocess.run(command, capture_output=True).returncode, 0)


if __name__ == '__main__':
    unittest.main()
