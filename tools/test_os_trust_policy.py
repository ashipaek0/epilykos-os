#!/usr/bin/env python3
"""Shared OS trust policy (epilykos_os_trust, plan task 4): root-signed
policy verification, the verified state read from the U-Boot environment,
monotonic admission of a newer policy, the generated RAUC keyring, and
revocation that survives running an older (retained) OS.

The environment is a scripted stand-in for fw_printenv/fw_setenv here;
the guarded native writers' generation and replication rules are tested
against the real tools in test_native_platform_env.py.
"""
import base64
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import textwrap
import unittest

sys.dont_write_bytecode = True
import os_trust_fixtures as fx  # noqa: E402
from os_trust_fixtures import trust  # noqa: E402

FAKE_ENV = textwrap.dedent('''\
    #!/usr/bin/env python3
    import json, os, sys
    path = os.environ['EPK_FAKE_ENV']
    state = json.load(open(path))
    mode = state.pop('_mode', '')
    if sys.argv[0].endswith('fw_printenv'):
        if mode == 'unreadable':
            sys.exit(1)
        print(''.join(f'{k}={v}\\n' for k, v in state.items() if not k.startswith('_')), end='')
        sys.exit(0)
    if mode == 'refuse':
        sys.exit(1)
    assert sys.argv[1:] == ['--script', '-'], sys.argv
    for line in sys.stdin.read().splitlines():
        name, _, value = line.partition(' ')
        state[name] = value
    if mode == 'lie':
        sys.exit(0)
    if mode:
        state['_mode'] = mode
    state['_writes'] = state.get('_writes', 0) + 1
    json.dump(state, open(path, 'w'))
''')


class Custodian(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.dir = Path(cls.temp.name)
        t = cls.t = fx.Trust(cls.dir / 'keys')
        t.index_key('dev-1')
        t.index_key('dev-2')
        t.ca('ca-1')
        t.ca('ca-2')
        t.leaf('leaf-1', 'ca-1')
        t.leaf('leaf-2', 'ca-1')
        cls.p1 = t.policy(1, ['dev-1'], ['ca-1'], ['leaf-1'])
        # Epoch 2 revokes the first delegated key and signing leaf.
        cls.p2 = t.policy(2, ['dev-2'], ['ca-1'], ['leaf-2'])
        cls.platform = fx.platform()

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def verify(self, pair, **kwargs):
        return trust.verify_policy(pair[0], pair[1], self.t.root_pem, board='raspberrypi3', channel='dev', **kwargs)


class PolicyTests(Custodian):
    def test_valid_policy_verifies(self):
        self.assertEqual(self.verify(self.p1)['trust_epoch'], 1)

    def test_signature_by_another_root_or_tampered_bytes_is_refused(self):
        other = fx.Trust(self.dir / 'other')
        other.index_key('dev-1')
        other.ca('ca-1')
        other.leaf('leaf-1', 'ca-1')
        raw, _ = self.p1
        with self.assertRaises(ValueError):
            trust.verify_policy(raw, other.sign(other.root_key, raw), self.t.root_pem, board='raspberrypi3',
                                channel='dev')
        with self.assertRaises(ValueError):
            self.verify((raw.replace(b'"trust_epoch":1', b'"trust_epoch":9'), self.p1[1]))

    def test_policy_for_another_channel_or_board_is_refused(self):
        stable = self.t.policy(1, ['dev-1'], ['ca-1'], ['leaf-1'], channel='stable')
        with self.assertRaises(ValueError):
            self.verify(stable)
        with self.assertRaises(ValueError):
            self.verify(self.t.policy(1, ['dev-1'], ['ca-1'], ['leaf-1'], board='raspberrypi4'))

    def test_unknown_schema_and_fields_are_refused(self):
        for override in ({'schema': 2}, {'extra': 1}, {'trust_epoch': 0}, {'rauc_leaf_spki_sha256': []},
                         {'index_keys': []}):
            with self.subTest(override=override), self.assertRaises(ValueError):
                self.verify(self.t.policy(1, ['dev-1'], ['ca-1'], ['leaf-1'], **override))

    def test_a_signing_leaf_is_not_a_trust_anchor(self):
        leaf_pem = self.t.leaves['leaf-1'][1].read_text()
        with self.assertRaises(ValueError):
            self.verify(self.t.policy(1, ['dev-1'], ['ca-1'], ['leaf-1'], rauc_cas=[leaf_pem]))

    def test_epochs_only_move_forward_and_same_epoch_must_be_identical(self):
        current = {'raw': self.p2[0], 'trust_epoch': 2}
        with self.assertRaises(ValueError):
            self.verify(self.p1, current=current)                       # rollback
        mutated = self.t.policy(2, ['dev-1', 'dev-2'], ['ca-1'], ['leaf-2'])
        with self.assertRaises(ValueError):
            self.verify(mutated, current=current)                       # same epoch, different bytes
        self.assertEqual(self.verify(self.p2, current=current)['trust_epoch'], 2)
        self.assertEqual(self.verify(self.p2, current={'raw': self.p1[0], 'trust_epoch': 1})['trust_epoch'], 2)


class StateTests(Custodian):
    def setUp(self):
        self.work = Path(tempfile.mkdtemp(dir=self.dir))
        self.envfile = self.work / 'env.json'
        for name in ('fw_printenv', 'fw_setenv'):
            path = self.work / name
            path.write_text(FAKE_ENV)
            path.chmod(path.stat().st_mode | stat.S_IXUSR)
        self.saved = trust.FW_PRINTENV, trust.FW_SETENV
        trust.FW_PRINTENV, trust.FW_SETENV = str(self.work / 'fw_printenv'), str(self.work / 'fw_setenv')
        os.environ['EPK_FAKE_ENV'] = str(self.envfile)
        self.put(fx.env_for(*self.p1, 1))

    def tearDown(self):
        trust.FW_PRINTENV, trust.FW_SETENV = self.saved

    def put(self, values, mode=''):
        self.envfile.write_text(json.dumps({**values, **({'_mode': mode} if mode else {})}))

    def env(self):
        return {k: v for k, v in json.loads(self.envfile.read_text()).items() if not k.startswith('_')}

    def state(self):
        return trust.current_state(self.platform, root_pem=self.t.root_pem)

    def test_current_state_is_the_verified_policy_and_floor(self):
        state = self.state()
        self.assertEqual((state['trust_epoch'], state['floor'], state['raw']), (1, 11, self.p1[0]))

    def test_missing_or_invalid_state_disables_updates_instead_of_resetting(self):
        cases = {
            'no policy': {'epk_trust_policy': None},
            'no floor': {'epk_os_floor': None},
            'scalar epoch differs': {'epk_trust_epoch': '2'},
            'bad signature': {'epk_trust_policy_sig': base64.b64encode(b'\0' * 64).decode()},
            'unknown state schema': {'epk_state_schema': '2'},
            'not base64': {'epk_trust_policy': '!!'},
        }
        for name, override in cases.items():
            with self.subTest(name):
                self.put(fx.env_for(*self.p1, 1, **override))
                with self.assertRaises(trust.TrustError):
                    self.state()
        self.put(fx.env_for(*self.p1, 1), mode='unreadable')
        with self.assertRaises(trust.TrustError):
            self.state()

    def test_a_policy_signed_by_another_root_in_the_env_is_refused(self):
        other = fx.Trust(self.dir / 'imposter')
        other.index_key('dev-1')
        other.ca('ca-1')
        other.leaf('leaf-1', 'ca-1')
        self.put(fx.env_for(*other.policy(5, ['dev-1'], ['ca-1'], ['leaf-1']), 5))
        with self.assertRaises(trust.TrustError):
            self.state()

    def test_newer_policy_is_persisted_and_read_back_in_one_batch(self):
        state = trust.admit_policy(*self.p2, self.platform, self.state(), root_pem=self.t.root_pem)
        self.assertEqual(state['trust_epoch'], 2)
        env = self.env()
        self.assertEqual(env['epk_trust_epoch'], '2')
        self.assertEqual(base64.b64decode(env['epk_trust_policy']), self.p2[0])
        self.assertEqual(env['epk_os_floor'], '11', 'trust advancement never moves the OS floor')

    def test_same_policy_is_rewritten_unchanged_and_older_or_mutated_is_refused(self):
        before = self.env()
        trust.admit_policy(*self.p1, self.platform, self.state(), root_pem=self.t.root_pem)
        self.assertEqual(self.env(), before)
        self.assertEqual(json.loads(self.envfile.read_text())['_writes'], 1,
                         'written again (replication), not skipped')
        trust.admit_policy(*self.p2, self.platform, self.state(), root_pem=self.t.root_pem)
        for pair in (self.p1, self.t.policy(2, ['dev-1'], ['ca-1'], ['leaf-1'])):
            with self.assertRaises(trust.TrustError):
                trust.admit_policy(*pair, self.platform, self.state(), root_pem=self.t.root_pem)
        self.assertEqual(self.state()['trust_epoch'], 2)

    def test_refused_or_unpersisted_write_is_never_reported_as_admitted(self):
        for mode in ('refuse', 'lie'):
            with self.subTest(mode=mode):
                self.put(fx.env_for(*self.p1, 1), mode=mode)
                with self.assertRaises(trust.TrustError):
                    trust.admit_policy(*self.p2, self.platform, self.state(), root_pem=self.t.root_pem)

    def test_keyring_is_exactly_the_policy_cas_and_never_stale(self):
        keyring = self.work / 'run/rauc/epilykos-keyring.pem'
        saved = trust.ROOT_PEM, trust.PLATFORM
        (self.work / 'root.pem').write_bytes(self.t.root_pem)
        (self.work / 'platform.json').write_bytes(trust.canonical(self.platform))
        trust.ROOT_PEM, trust.PLATFORM = str(self.work / 'root.pem'), str(self.work / 'platform.json')
        try:
            self.assertEqual(trust.keyring_main(keyring), 0)
            self.assertEqual(keyring.read_bytes(), self.t.cas['ca-1'][1].read_bytes())
            self.assertEqual(stat.S_IMODE(keyring.stat().st_mode), 0o644)
            # Policy state becomes unverifiable: the old keyring must go, not linger.
            self.put(fx.env_for(*self.p1, 1, epk_trust_epoch='7'))
            self.assertEqual(trust.keyring_main(keyring), 1)
            self.assertFalse(keyring.exists())
        finally:
            trust.ROOT_PEM, trust.PLATFORM = saved

    def test_revocation_survives_running_the_retained_older_os(self):
        """Epoch 2 is admitted while running sequence 12; the box then falls
        back to the retained sequence-11 slot. That slot's code reads the same
        shared state, so a forgery by the revoked key at a higher sequence
        is still refused there, and so is an entry under the old epoch."""
        trust.admit_policy(*self.p2, fx.platform(sequence=12), self.state(), root_pem=self.t.root_pem)
        retained = fx.platform(sequence=11)
        state = trust.current_state(retained, root_pem=self.t.root_pem)
        forged = self.t.entry('dev-1', sequence=99, epoch=2)
        with self.assertRaises(trust.TrustError):
            trust.verify_os_release(*forged, retained, state['policy'])
        old_epoch = self.t.entry('dev-2', sequence=99, epoch=1)
        with self.assertRaises(trust.TrustError):
            trust.verify_os_release(*old_epoch, retained, state['policy'])
        good = self.t.entry('dev-2', sequence=13, epoch=2)
        self.assertEqual(trust.verify_os_release(*good, retained, state['policy'])['sequence'], 13)
        self.assertNotIn(self.t.leaves['leaf-1'][2], state['policy']['rauc_leaf_spki_sha256'])


class ImageWiringTests(unittest.TestCase):
    BOARD = Path(__file__).resolve().parents[1] / 'os/board/epilykos/raspberrypi3'

    def test_protocol2_rauc_uses_only_the_generated_keyring_and_verity(self):
        import configparser
        conf = configparser.ConfigParser(interpolation=None)
        conf.read(self.BOARD / 'protocol2-overlay/etc/rauc/system.conf')
        self.assertEqual(conf['keyring']['path'], trust.KEYRING)
        self.assertEqual(conf['keyring']['use-bundle-signing-time'], 'true')
        self.assertNotIn('directory', conf['keyring'])
        self.assertEqual(conf['system']['bundle-formats'], 'verity')
        self.assertEqual(conf['system']['activate-installed'], 'false')
        base = configparser.ConfigParser(interpolation=None)
        base.read(self.BOARD / 'rootfs-overlay/etc/rauc/system.conf')
        for slot in ('slot.rootfs.0', 'slot.rootfs.1'):
            self.assertEqual(dict(conf[slot]), dict(base[slot]))

    def test_keyring_is_regenerated_before_every_rauc_start_without_blocking_it(self):
        dropin = (self.BOARD / 'protocol2-overlay/usr/lib/systemd/system/rauc.service.d/'
                  '20-epilykos-keyring.conf').read_text().splitlines()
        self.assertIn('ExecStartPre=-/usr/bin/epilykos-os-keyring', dropin)
        launcher = self.BOARD / 'protocol2-overlay/usr/bin/epilykos-os-keyring'
        self.assertTrue(os.access(launcher, os.X_OK))
        self.assertIn('keyring_main()', launcher.read_text())

    def test_kernel_has_dm_verity_built_in(self):
        fragment = (self.BOARD / 'linux.fragment').read_text().splitlines()
        for line in ('CONFIG_BLK_DEV_DM=y', 'CONFIG_DM_VERITY=y', 'CONFIG_BLK_DEV_LOOP=y', 'CONFIG_SQUASHFS=y'):
            self.assertIn(line, fragment)
        protocol2 = (self.BOARD / 'protocol2.config').read_text().splitlines()
        self.assertIn('BR2_PACKAGE_HOST_RAUC=y', protocol2)


if __name__ == '__main__':
    unittest.main()
