#!/usr/bin/env python3
"""Publishing OS update packages (tools/os_release.py, plan task 11): the
fail-closed gate, sequence allocation that never reuses a number, the exact
upload set re-verified (tampered, unsigned, wrong signer, stale epoch,
mismatched URL, collisions), read-back comparison, and the workflow wiring
that moves the pointer last.
"""
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True
import os_trust_fixtures as fx  # noqa: E402
from os_trust_fixtures import trust  # noqa: E402
import os_release  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
REPO = 'https://github.com/ashipaek0/epilykos-os/releases/download'
GOOD = {'channel': 'stable', 'ref': 'refs/heads/main', 'event': 'push', 'source_on_main': 'true', 'boot_protocol': '2',
        'trust_label': 'maintainer-trust', 'build': 'success', 'rootfs_uploaded': 'true', 'sequence': '12',
        'publication_enabled': 'true', 'image_from_run': ''}


class GateTests(unittest.TestCase):
    def test_everything_must_hold(self):
        self.assertEqual(os_release.decide(GOOD), {'publish': True, 'blocking': []})
        cases = {'boot_protocol': '1', 'trust_label': 'test-trust', 'build': 'failure', 'rootfs_uploaded': 'false',
                 'sequence': '0', 'publication_enabled': '', 'image_from_run': '123', 'source_on_main': 'false'}
        for key, value in cases.items():
            with self.subTest(key=key):
                self.assertFalse(os_release.decide({**GOOD, key: value})['publish'])

    def test_channels_only_from_their_reviewed_branch(self):
        self.assertFalse(os_release.decide({**GOOD, 'ref': 'refs/heads/dev'})['publish'], 'stable only from main')
        self.assertFalse(os_release.decide({**GOOD, 'event': 'pull_request'})['publish'])
        dev = {**GOOD, 'channel': 'dev', 'ref': 'refs/heads/dev', 'event': 'workflow_dispatch'}
        self.assertTrue(os_release.decide(dev)['publish'])
        self.assertFalse(os_release.decide({**dev, 'ref': 'refs/heads/main'})['publish'], 'no cross-channel signing')
        self.assertFalse(os_release.decide({**dev, 'ref': 'refs/heads/feature'})['publish'])
        self.assertTrue(os_release.decide({**GOOD, 'event': 'release', 'ref': 'refs/tags/v0.7'})['publish'])


class PackageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.dir = Path(cls.temp.name)
        t = cls.t = fx.Trust(cls.dir / 'keys', channel='stable')
        t.index_key('stable-1')
        t.index_key('other')
        t.ca('ca-1')
        t.leaf('leaf-1', 'ca-1')
        t.leaf('leaf-2', 'ca-1')
        cls.trust_dir = cls.dir / 'trust'
        cls.trust_dir.mkdir()
        raw, sig = t.policy(1, ['stable-1'], ['ca-1'], ['leaf-1'])
        for name, data in (('os-policy.json', raw), ('os-policy.sig', sig), ('os-index-root.pem', t.root_pem)):
            (cls.trust_dir / name).write_bytes(data)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def package(self, name, leaf='leaf-1', key='stable-1', epoch=1, sequence=12, url=None, tamper=False):
        package = self.dir / name
        package.mkdir()
        bundle = fx.signed_bundle(self.t, leaf, package / 'epilykos-os-0.7-raspberrypi3.raucb', sequence=sequence,
                                  channel='stable', epoch=epoch)
        data = bundle.read_bytes()
        url = url or f'{REPO}/os-stable-{sequence}/{bundle.name}'
        raw, sig = self.t.entry(key, sequence=sequence, epoch=epoch, channel='stable',
                                bundle={'url': url, 'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()})
        (package / 'manifest.raucm').unlink()
        (package / 'epilykos-os-0.7-raspberrypi3.entry.json').write_bytes(raw)
        (package / 'epilykos-os-0.7-raspberrypi3.entry.sig').write_bytes(sig)
        if tamper:
            bundle.write_bytes(data[:-200] + b'x' * 200)
        return package

    def test_upload_set_is_exact_and_verified(self):
        out = self.dir / 'out-good'
        result = os_release.assets(self.package('good'), self.trust_dir, 'stable', out)
        self.assertEqual((result['tag'], result['pointer'], result['sequence']), ('os-stable-12', 'os-stable', 12))
        self.assertEqual(sorted(p.name for p in (out / 'pointer').iterdir()),
                         ['os-index-stable.json', 'os-index-stable.json.sig', 'os-policy.json', 'os-policy.sig'])
        self.assertEqual((out / 'pointer/os-index-stable.json').read_bytes(),
                         (out / 'package/epilykos-os-0.7-raspberrypi3.entry.json').read_bytes(),
                         'the pointer is the exact signed entry')
        copy = self.dir / 'downloaded'
        shutil.copytree(out / 'pointer', copy)
        self.assertEqual(len(os_release.compare(out / 'pointer', copy)), 4)
        (copy / 'os-policy.sig').write_bytes(b'\0' * 64)
        with self.assertRaisesRegex(ValueError, 'did not read back'):
            os_release.compare(out / 'pointer', copy)
        (copy / 'os-policy.sig').unlink()
        with self.assertRaises(ValueError):
            os_release.compare(out / 'pointer', copy)

    def test_refusals(self):
        cases = {
            'tampered': dict(tamper=True),
            'unlisted leaf': dict(leaf='leaf-2'),
            'unlisted index key': dict(key='other'),
            'stale epoch': dict(epoch=2),
            'url elsewhere': dict(url=f'{REPO}/os-stable-11/epilykos-os-0.7-raspberrypi3.raucb'),
        }
        for name, kwargs in cases.items():
            with self.subTest(name), self.assertRaises(ValueError):
                os_release.assets(self.package(name.replace(' ', '-'), **kwargs), self.trust_dir, 'stable',
                                  self.dir / f'out-{name.replace(" ", "-")}')
        with self.assertRaisesRegex(ValueError, 'never reused'):
            os_release.assets(self.package('collide'), self.trust_dir, 'stable', self.dir / 'out-collide',
                              existing=['epilykos-os-0.7-raspberrypi3.raucb'])
        with self.assertRaises(ValueError):
            os_release.assets(self.package('wrong-channel'), self.trust_dir, 'dev', self.dir / 'out-dev')

    def test_sequences_are_never_reused(self):
        self.assertEqual(os_release.next_sequence(None, 'stable'), 1)
        raw, _ = self.t.entry('stable-1', sequence=12, channel='stable')
        self.assertEqual(os_release.next_sequence(raw, 'stable'), 13)
        os_release.check_sequence(raw, 'stable', 13)
        for sequence in (12, 5):
            with self.assertRaises(ValueError):
                os_release.check_sequence(raw, 'stable', sequence)
        with self.assertRaises(ValueError):
            os_release.next_sequence(raw, 'dev')


class TestPackageTests(unittest.TestCase):
    """A TEST-ONLY build signs its run page as the bundle URL; for the
    spare-card acceptance the entry is re-signed for its test release."""

    def setUp(self):
        import os_test_trust
        from types import SimpleNamespace
        self.temp = tempfile.TemporaryDirectory()
        self.dir = Path(self.temp.name)
        self.trust = self.dir / 'trust'
        os_test_trust.generate(self.trust, 'dev', 'dev-test-1')
        self.package = self.dir / 'package'
        self.package.mkdir()
        leaf = SimpleNamespace(leaves={'leaf': (self.trust / 'private/rauc-signing.key', self.trust / 'rauc-signing.pem', None)})
        bundle = fx.signed_bundle(leaf, 'leaf', self.package / 'epilykos-os-0.9-raspberrypi3.raucb', sequence=2)
        (self.package / 'manifest.raucm').unlink()
        data = bundle.read_bytes()
        entry = {'schema': 1, 'board': 'raspberrypi3', 'channel': 'dev', 'sequence': 2, 'trust_epoch': 1,
                 'index_key_id': 'dev-test-1', 'version': '0.9', 'boot_protocol': 2, 'min_host_api': 2,
                 'bundle': {'url': 'https://github.com/ashipaek0/epilykos-os/actions/runs/7/' + bundle.name,
                            'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()},
                 'notes_url': 'https://github.com/ashipaek0/epilykos-os/actions/runs/7'}
        (self.package / 'epilykos-os-0.9-raspberrypi3.entry.json').write_bytes(trust.canonical(entry))
        (self.package / 'epilykos-os-0.9-raspberrypi3.entry.sig').write_bytes(b'\0' * 64)

    def tearDown(self):
        self.temp.cleanup()

    def test_resigned_for_its_test_release_and_publishable_there_only(self):
        with self.assertRaises(ValueError):
            os_release.assets(self.package, self.trust, 'dev', self.dir / 'out-before', tag='os-dev-test-7')
        result = os_release.test_entry(self.package, self.trust, 'ashipaek0/epilykos-os', 'os-dev-test-7')
        self.assertEqual(result['url'], f'{REPO}/os-dev-test-7/epilykos-os-0.9-raspberrypi3.raucb')
        out = os_release.assets(self.package, self.trust, 'dev', self.dir / 'out', tag='os-dev-test-7')
        self.assertEqual((out['tag'], out['pointer'], out['sequence']), ('os-dev-test-7', 'os-dev', 2))
        with self.assertRaises(ValueError):
            os_release.assets(self.package, self.trust, 'dev', self.dir / 'out-2')   # not os-dev-2

    def test_never_with_maintainer_trust_or_odd_tags(self):
        maintainer = self.dir / 'maintainer'
        shutil.copytree(self.trust, maintainer)
        (maintainer / 'TEST-ONLY.txt').unlink()
        with self.assertRaisesRegex(ValueError, 'TEST-ONLY'):
            os_release.test_entry(self.package, maintainer, 'ashipaek0/epilykos-os', 'os-dev-test-7')
        for repository, tag in (('ashipaek0/epilykos-os', 'os-dev-2'), ('a b', 'os-dev-test-7'),
                                ('ashipaek0/epilykos-os', 'os-dev-test-7/../x')):
            with self.subTest(tag=tag), self.assertRaises(ValueError):
                os_release.test_entry(self.package, self.trust, repository, tag)
        with self.assertRaises(ValueError):
            os_release.assets(self.package, maintainer, 'dev', self.dir / 'o', tag='os-dev-test-7')


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        import yaml
        self.workflow = yaml.safe_load((ROOT / '.github/workflows/os-build.yml').read_text())
        self.job = self.workflow['jobs']['os-package']

    def test_publishing_job_is_protected_and_serialised(self):
        self.assertEqual(self.job['environment'], 'os-${{ needs.build.outputs.channel }}')
        self.assertEqual(self.job['permissions'], {'contents': 'write'})
        self.assertEqual(self.job['if'], "needs.build.outputs.os_publish == 'true'")
        self.assertFalse(self.job['concurrency']['cancel-in-progress'])
        self.assertNotIn('pull_request', self.workflow.get(True, {}))

    def test_pointer_moves_last_after_read_back(self):
        names = [step.get('name', '') for step in self.job['steps']]
        order = ['Package from the build\'s rootfs', 'Upload the package release', 'Read the package back',
                 'Move the channel pointer', 'Read the pointer back']
        positions = [names.index(name) for name in order]
        self.assertEqual(positions, sorted(positions))
        text = json.dumps(self.job)
        self.assertNotIn('os-index-root.key', text, 'no root private key in CI')
        self.assertIn('check-sequence', text)

    def test_test_packages_publish_only_on_request_and_only_test_trust(self):
        job = self.workflow['jobs']['test-package-publish']
        self.assertEqual(job['if'], "github.event.inputs.publish_test_package_from_run != ''")
        self.assertEqual(job['permissions'], {'contents': 'write', 'actions': 'read'})
        text = json.dumps(job)
        self.assertIn('protocol2-test-trust', text)
        self.assertIn('test-entry', text)
        self.assertNotIn('secrets.', text, 'TEST-ONLY keys only, never the signing environment')
        names = [step.get('name', '') for step in job['steps']]
        self.assertLess(names.index('Read the package back'), names.index('Move the channel pointer'))
        self.assertIn('publish_test_package_from_run', self.workflow['jobs']['build']['if'])

    def test_the_package_is_made_from_the_same_rootfs_as_the_card(self):
        build = json.dumps(self.workflow['jobs']['build'])
        self.assertIn('protocol2-rootfs', build)
        self.assertIn('protocol2-rootfs', json.dumps(self.job))


if __name__ == '__main__':
    unittest.main()
