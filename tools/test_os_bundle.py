#!/usr/bin/env python3
"""OS update packages against real RAUC 1.15.2 (plan task 4).

Builds signed verity bundles with the real `rauc bundle`, checks them with
`rauc info` under the protocol-2 system.conf and with the box's own checks
(epilykos_os_trust.check_bundle), and refuses: tampered payload, unsigned,
wrong CA, an allowed CA's leaf missing from the policy allowlist, a manifest
that disagrees with its entry, extra images, hooks, custom handlers, plain
bundles, over-capacity or wrong-channel rootfs. Every refusal here happens
before anything could write a slot.

Needs a RAUC binary built with bundle creation (--rauc), mksquashfs,
mke2fs and debugfs: run by `make test-native-env` and the native CI job.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True
import os_trust_fixtures as fx  # noqa: E402
from os_trust_fixtures import trust  # noqa: E402
import build_os_bundle  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SYSTEM_CONF = ROOT / 'os/board/epilykos/raspberrypi3/protocol2-overlay/etc/rauc/system.conf'
RAUC = None
URL = 'https://github.com/ashipaek0/epilykos-os/releases/download/v0.7'
NOTES = 'https://github.com/ashipaek0/epilykos-os/releases/tag/v0.7'


class BundleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.dir = Path(cls.temp.name)
        t = cls.t = fx.Trust(cls.dir / 'keys')
        t.index_key('dev-1')
        t.ca('ca-1')
        t.ca('ca-2')
        t.leaf('leaf-1', 'ca-1')
        t.leaf('leaf-unlisted', 'ca-1')
        t.leaf('leaf-other-ca', 'ca-2')
        cls.policy_pair = t.policy(1, ['dev-1'], ['ca-1'], ['leaf-1'])
        cls.policy = trust.verify_policy(*cls.policy_pair, t.root_pem, board='raspberrypi3', channel='dev')
        cls.keyring = trust.write_keyring(cls.policy, cls.dir / 'keyring.pem')
        cls.platform = fx.platform(sequence=12, os_version='0.7')
        cls.rootfs = fx.rootfs(cls.dir / 'rootfs.ext4', cls.platform)
        cls.conf = cls.dir / 'system.conf'
        cls.conf.write_text(cls.system_conf(cls.keyring))
        cls.good = cls.build(cls.dir / 'good')
        cls.bundle = cls.good / 'epilykos-os-0.7-raspberrypi3.raucb'
        cls.entry_raw = (cls.good / 'epilykos-os-0.7-raspberrypi3.entry.json').read_bytes()
        cls.entry = json.loads(cls.entry_raw)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    @classmethod
    def system_conf(cls, keyring):
        """The protocol-2 system.conf with this test's keyring and RAUC data
        directory (CI does not run as root, so never /data/rauc)."""
        data = cls.dir / 'rauc-data'
        data.mkdir(exist_ok=True)
        return SYSTEM_CONF.read_text().replace(trust.KEYRING, str(keyring)) \
            .replace('data-directory=/data/rauc', f'data-directory={data}')

    @classmethod
    def build(cls, output, leaf='leaf-1', rootfs=None, policy=None):
        _, cert_pem, _ = cls.t.leaves[leaf]
        cls.last = build_os_bundle.build(
            rootfs=rootfs or cls.rootfs, output=output, rauc=RAUC, cert=cert_pem, key=cls.t.leaves[leaf][0],
            index_key=cls.t.index['dev-1'][0], index_key_id='dev-1',
            policy_raw=(policy or cls.policy_pair)[0], policy_sig=(policy or cls.policy_pair)[1],
            root_pem=cls.t.root_pem, url_base=URL, notes_url=NOTES)
        return Path(output)

    def raw_bundle(self, name, manifest, leaf='leaf-1', extra=()):
        """A bundle made directly with rauc, bypassing build_os_bundle's checks."""
        content = self.dir / f'{name}.content'
        content.mkdir()
        shutil.copyfile(self.rootfs, content / 'rootfs.ext4')
        for filename in extra:
            (content / filename).write_text('#!/bin/sh\nexit 0\n')
        (content / 'manifest.raucm').write_text(manifest)
        bundle = self.dir / f'{name}.raucb'
        key, cert, _ = self.t.leaves[leaf]
        result = subprocess.run([RAUC, 'bundle', '--cert', cert, '--key', key, content, bundle],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return bundle

    def manifest(self, **meta):
        values = {'board': 'raspberrypi3', 'channel': 'dev', 'sequence': 12, 'boot_protocol': 2, 'min_host_api': 2,
                  'trust_epoch': 1, **meta}
        return build_os_bundle.manifest(self.platform, 2, 1).split('[meta.epilykos]')[0] + '[meta.epilykos]\n' + \
            ''.join(f'{k}={v}\n' for k, v in values.items() if v is not None)

    def entry_for(self, bundle):
        size, sha = trust.file_identity(bundle)
        return {**self.entry, 'bundle': {**self.entry['bundle'], 'size': size, 'sha256': sha}}

    def rauc_info(self, bundle):
        return subprocess.run([RAUC, '--conf', str(self.conf), 'info', str(bundle)], capture_output=True, text=True)

    def refused(self, bundle, entry=None):
        with self.assertRaises(trust.TrustError):
            trust.check_bundle(bundle, entry or self.entry_for(bundle), self.policy, self.keyring)

    # -- the good package ------------------------------------------------------
    def test_package_is_the_rootfs_signed_and_authorized(self):
        self.assertEqual(sorted(p.name for p in self.good.iterdir()),
                         ['epilykos-os-0.7-raspberrypi3.entry.json', 'epilykos-os-0.7-raspberrypi3.entry.sig',
                          'epilykos-os-0.7-raspberrypi3.raucb'])
        self.assertEqual((self.entry['sequence'], self.entry['channel'], self.entry['trust_epoch']), (12, 'dev', 1))
        self.assertEqual(self.entry['bundle']['url'], f'{URL}/epilykos-os-0.7-raspberrypi3.raucb')
        info = self.rauc_info(self.bundle)
        self.assertEqual(info.returncode, 0, info.stderr)
        self.assertIn('Bundle Format:  verity', info.stdout)
        manifest, spki = trust.bundle_signer(self.bundle, self.keyring)
        self.assertEqual(spki, self.t.leaves['leaf-1'][2])
        parser = trust.check_manifest(manifest, self.entry)
        self.assertEqual(parser['image.rootfs']['sha256'], trust.file_identity(self.rootfs)[1],
                         'the package carries the same bytes as the card slots')
        trust.check_bundle(self.bundle, self.entry, self.policy, self.keyring)
        signature = (self.good / 'epilykos-os-0.7-raspberrypi3.entry.sig').read_bytes()
        trust.verify_os_release(self.entry_raw, signature, fx.platform(sequence=11), self.policy)

    def test_cli_verifies_the_published_files_together(self):
        work = self.dir / 'cli'
        work.mkdir()
        for name, data in {'root.pem': self.t.root_pem, 'policy.json': self.policy_pair[0],
                           'policy.sig': self.policy_pair[1],
                           'platform.json': trust.canonical(fx.platform(sequence=11))}.items():
            (work / name).write_bytes(data)
        result = subprocess.run([sys.executable, str(ROOT / 'tools/verify_os_release.py'),
                                 '--root', str(work / 'root.pem'), '--policy', str(work / 'policy.json'),
                                 '--policy-sig', str(work / 'policy.sig'), '--platform', str(work / 'platform.json'),
                                 '--entry', str(self.good / 'epilykos-os-0.7-raspberrypi3.entry.json'),
                                 '--entry-sig', str(self.good / 'epilykos-os-0.7-raspberrypi3.entry.sig'),
                                 '--bundle', str(self.bundle), '--floor', '11'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['signer_spki_sha256'], self.t.leaves['leaf-1'][2])

    # -- bytes and signatures -------------------------------------------------
    def test_tampered_payload_is_refused_before_rauc(self):
        tampered = self.dir / 'tampered.raucb'
        shutil.copyfile(self.bundle, tampered)
        with tampered.open('r+b') as stream:
            stream.seek(4096)
            byte = stream.read(1)
            stream.seek(4096)
            stream.write(bytes([byte[0] ^ 0xFF]))
        with self.assertRaisesRegex(trust.TrustError, 'differs from its authorization'):
            trust.check_bundle(tampered, self.entry, self.policy, self.keyring)

    def test_unsigned_bundle_is_refused(self):
        unsigned = self.dir / 'unsigned.raucb'
        size = self.bundle.stat().st_size
        signature_size = int.from_bytes(self.bundle.read_bytes()[-8:], 'big')
        with self.bundle.open('rb') as source, unsigned.open('wb') as target:
            target.write(source.read(size - 8 - signature_size))
        self.refused(unsigned)
        self.assertNotEqual(self.rauc_info(unsigned).returncode, 0)

    def test_wrong_ca_is_refused_by_rauc_and_by_the_box(self):
        bundle = self.raw_bundle('wrong-ca', self.manifest(), leaf='leaf-other-ca')
        self.assertNotEqual(self.rauc_info(bundle).returncode, 0)
        self.refused(bundle)
        with self.assertRaises(ValueError):
            self.build(self.dir / 'wrong-ca-package', leaf='leaf-other-ca')
        self.assertFalse((self.dir / 'wrong-ca-package').exists())

    def test_unlisted_leaf_of_an_allowed_ca_is_refused_though_rauc_accepts_it(self):
        bundle = self.raw_bundle('unlisted', self.manifest(), leaf='leaf-unlisted')
        self.assertEqual(self.rauc_info(bundle).returncode, 0, 'native CMS alone would accept it')
        with self.assertRaisesRegex(trust.TrustError, 'signer the trust policy does not list'):
            trust.check_bundle(bundle, self.entry_for(bundle), self.policy, self.keyring)

    def test_certificates_are_checked_at_the_bundle_signing_time(self):
        """use-bundle-signing-time (design §6.1): a box with no clock, or a
        clock far ahead, still accepts a bundle signed while its leaf was
        valid; a bundle signed with an already expired leaf is refused."""
        if not shutil.which('faketime'):
            self.skipTest('faketime is not installed')
        then = ['faketime', '2021-06-15 12:00:00']
        # Certificates get explicit past dates (openssl ca), not faked clocks;
        # the whole chain must be valid at the signing time.
        ca_key, ca_pem = self.dated_cert('old-ca', '20210101000000Z', '20460101000000Z', ca=True)
        key, pem = self.dated_cert('old-leaf', '20210601000000Z', '20210701000000Z', issuer=(ca_pem, ca_key))
        conf = self.dir / 'old-ca.conf'
        conf.write_text(self.system_conf(ca_pem))
        content = self.dir / 'old.content'
        content.mkdir()
        shutil.copyfile(self.rootfs, content / 'rootfs.ext4')
        (content / 'manifest.raucm').write_text(self.manifest())
        signed_then, signed_now = self.dir / 'signed-then.raucb', self.dir / 'signed-now.raucb'
        subprocess.run([*then, RAUC, 'bundle', '--cert', pem, '--key', key, content, signed_then], check=True,
                       capture_output=True)
        subprocess.run([RAUC, 'bundle', '--cert', pem, '--key', key, content, signed_now], capture_output=True)
        cms = subprocess.run(['openssl', 'cms', '-cmsout', '-print', '-inform', 'DER', '-in', '/dev/stdin'],
                             input=trust.bundle_signature(signed_then), capture_output=True).stdout
        if b'2021' not in cms.split(b'signingTime', 1)[-1][:200]:
            self.skipTest('faketime does not change the signing time on this host')
        def info(bundle, config=conf):
            return subprocess.run([RAUC, '--conf', str(config), 'info', str(bundle)], capture_output=True, text=True)
        result = info(signed_then)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotEqual(info(signed_now).returncode, 0, 'expired when it was signed')
        strict = self.dir / 'strict.conf'
        strict.write_text(conf.read_text().replace('use-bundle-signing-time=true', ''))
        result = subprocess.run([RAUC, '--conf', str(strict), 'info', str(signed_then)], capture_output=True)
        self.assertNotEqual(result.returncode, 0, 'without the option the same bundle is refused today')

    def dated_cert(self, name, start, end, ca=False, issuer=None):
        """An EC P-256 certificate with exactly this validity window."""
        work = self.dir / f'{name}.db'
        work.mkdir()
        (work / 'index.txt').touch()
        (work / 'serial').write_text('1000\n')
        key, csr, pem = self.dir / f'{name}.key', self.dir / f'{name}.csr', self.dir / f'{name}.pem'
        subprocess.run(['openssl', 'req', '-new', '-newkey', 'ec', '-pkeyopt', 'ec_paramgen_curve:P-256', '-nodes',
                        '-keyout', key, '-out', csr, '-subj', f'/CN=TEST-ONLY {name}'], check=True,
                       capture_output=True)
        extensions = ('basicConstraints=critical,CA:TRUE\nkeyUsage=critical,keyCertSign,cRLSign\n' if ca else
                      'basicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature\n')
        config = work / 'ca.cnf'
        config.write_text(f'[ca]\ndefault_ca=d\n[d]\ndatabase={work}/index.txt\nserial={work}/serial\n'
                          f'new_certs_dir={work}\ndefault_md=sha256\npolicy=p\nunique_subject=no\n'
                          f'[p]\ncommonName=supplied\n[x]\n{extensions}')
        signer = ['-selfsign', '-keyfile', key] if issuer is None else ['-cert', issuer[0], '-keyfile', issuer[1]]
        result = subprocess.run(['openssl', 'ca', '-batch', '-notext', '-config', config, '-extensions', 'x',
                                 '-in', csr, '-out', pem, '-startdate', start, '-enddate', end, *signer],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return key, pem

    # -- manifest agreement ---------------------------------------------------
    def test_manifest_must_agree_with_its_entry(self):
        for field, value in (('sequence', 13), ('channel', 'stable'), ('trust_epoch', 2), ('min_host_api', 1),
                             ('boot_protocol', 1), ('board', None)):
            with self.subTest(field=field):
                bundle = self.raw_bundle(f'meta-{field}', self.manifest(**{field: value}))
                with self.assertRaisesRegex(trust.TrustError, f'disagree on {field}'):
                    trust.check_bundle(bundle, self.entry_for(bundle), self.policy, self.keyring)

    def test_extra_images_hooks_and_handlers_are_refused(self):
        base = self.manifest()
        cases = {
            'extra-image': (base + '\n[image.bootloader]\nfilename=extra.img\n', ['extra.img']),
            'install-hooks': (base.replace('filename=rootfs.ext4', 'filename=rootfs.ext4\nhooks=post-install') +
                              '\n[hooks]\nfilename=hook.sh\n', ['hook.sh']),
            'handler': (base + '\n[handler]\nfilename=hook.sh\n', ['hook.sh']),
        }
        for name, (manifest, extra) in cases.items():
            with self.subTest(name):
                bundle = self.raw_bundle(name, manifest, extra=extra)
                self.refused(bundle)

    def test_plain_bundles_are_refused_by_config_and_by_the_box(self):
        bundle = self.raw_bundle('plain', self.manifest().replace('format=verity', 'format=plain'))
        info = self.rauc_info(bundle)
        self.assertNotEqual(info.returncode, 0)
        self.assertIn("Bundle format 'plain' not allowed", info.stderr + info.stdout)
        self.refused(bundle)

    def test_without_a_verified_keyring_rauc_refuses_bundles_but_still_marks_good(self):
        """epilykos-os-keyring removes the keyring when the policy does not
        verify. RAUC must then still confirm slots (boot health relies on
        mark-good) while refusing every bundle."""
        work = self.dir / 'no-keyring'
        work.mkdir()
        (work / 'fw_printenv').write_text('#!/bin/sh\nprintf "BOOT_ORDER=A B\\nBOOT_A_LEFT=3\\nBOOT_B_LEFT=3\\n"\n')
        (work / 'fw_setenv').write_text(f'#!/bin/sh\necho "$@" >> {work}/set.log\n')
        for name in ('fw_printenv', 'fw_setenv'):
            (work / name).chmod(0o755)
        (work / 'a').touch()
        (work / 'b').touch()
        conf = work / 'system.conf'
        conf.write_text(SYSTEM_CONF.read_text()
                        .replace(trust.KEYRING, str(work / 'absent.pem'))
                        .replace('/dev/disk/by-partuuid/45504c4b-02', str(work / 'a'))
                        .replace('/dev/disk/by-partuuid/45504c4b-03', str(work / 'b'))
                        .replace('data-directory=/data/rauc', f'data-directory={work}/data'))
        env = {**os.environ, 'PATH': f'{work}{os.pathsep}{os.environ["PATH"]}'}
        good = subprocess.run([RAUC, '--conf', str(conf), 'status', '--override-boot-slot=A', 'mark-good', 'booted'],
                              capture_output=True, text=True, env=env)
        self.assertEqual(good.returncode, 0, good.stderr)
        self.assertIn('BOOT_A_LEFT 3', (work / 'set.log').read_text())
        info = subprocess.run([RAUC, '--conf', str(conf), 'info', str(self.bundle)], capture_output=True, text=True,
                              env=env)
        self.assertNotEqual(info.returncode, 0)

    # -- what can be packaged ---------------------------------------------------
    def test_over_capacity_rootfs_is_refused_before_bundling(self):
        big = self.dir / 'big.ext4'
        shutil.copyfile(self.rootfs, big)
        with big.open('r+b') as stream:
            stream.truncate(trust.SLOT_BYTES + 1)
        with self.assertRaisesRegex(ValueError, '1536 MiB'):
            self.build(self.dir / 'big-package', rootfs=big)
        self.assertFalse((self.dir / 'big-package').exists())

    def test_rootfs_for_another_channel_or_protocol_is_refused(self):
        stable = fx.rootfs(self.dir / 'stable.ext4', fx.platform(channel='stable', sequence=12))
        with self.assertRaises(ValueError):
            self.build(self.dir / 'stable-package', rootfs=stable)
        bare = self.dir / 'bare.ext4'
        subprocess.run(['mke2fs', '-q', '-F', '-t', 'ext4', str(bare), '8M'], check=True)
        with self.assertRaisesRegex(ValueError, 'not a protocol-2 build'):
            self.build(self.dir / 'bare-package', rootfs=bare)
        leftovers = [p.name for p in self.dir.iterdir() if p.name.startswith('.epk-package-')]
        self.assertEqual(leftovers, [])

    def test_existing_output_is_never_replaced(self):
        with self.assertRaisesRegex(ValueError, 'already exists'):
            self.build(self.good)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--rauc', type=Path, required=True, help='RAUC 1.15.2 built with bundle creation')
    args, remaining = parser.parse_known_args()
    RAUC = str(args.rauc.resolve())
    unittest.main(argv=[__file__, *remaining])
