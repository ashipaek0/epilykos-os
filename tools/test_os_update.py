#!/usr/bin/env python3
"""OS update jobs on the box (epilykos_os_update; plan tasks 5–6).

Real signed packages (CMS made with OpenSSL, the same layout RAUC appends),
a local HTTP server with Range/If-Range and fault injection, and stand-ins
for RAUC, systemctl, busctl, podman and the U-Boot environment tools:

- check/install admission: confirmed healthy slot, guard-v1 app, space,
  newer than floor and running slot; idempotent by request ID
- download: 1 MiB streaming, resume only with a validator and the exact
  206 Content-Range; ignored Range, changed validator, wrong range, early
  end, oversized or tampered bytes; DATA filling up; cancellation
- install: RAUC gets the job as transaction ID; the slot is verified
  independently and must stay non-bootable; failures mark it bad, and an
  unprovable state is `reconciling`, never a guessed success
- worker or RAUC lost mid-install: only the recorded RAUC writer can
  complete it; a killed worker resumes its download
- the guard: pending before the maintenance lease is released; hostd's
  conflicting actions refused; protocol-1 cards untouched
"""
import base64
import fcntl
import hashlib
import http.server
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import unittest
import unittest.mock

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
OVERLAY = ROOT / 'os/board/epilykos/raspberrypi3/rootfs-overlay'
T = Path(tempfile.mkdtemp(prefix='epilykos-os-update-'))
HOST = 'https://updates.invalid'
for name, value in {
    'EPILYKOS_OS_UPDATE_DIR': T / 'os-updater', 'EPILYKOS_OS_FEED': HOST + '/os-{channel}',
    'EPILYKOS_OS_HEALTH': T / 'os-health.json', 'EPILYKOS_CMDLINE': T / 'cmdline',
    'EPILYKOS_RAUC': T / 'bin/rauc', 'EPILYKOS_SYSTEMCTL': T / 'bin/systemctl', 'EPILYKOS_BUSCTL': T / 'bin/busctl',
    'EPILYKOS_PODMAN': T / 'bin/podman', 'EPILYKOS_SLOT_A': T / 'slot-a', 'EPILYKOS_SLOT_B': T / 'slot-b',
    'EPILYKOS_MAINTENANCE_LOCK': T / 'maintenance.lock', 'EPILYKOS_PLATFORM': T / 'platform.json',
    'EPILYKOS_OS_ROOT': T / 'os-index-root.pem', 'EPILYKOS_FW_PRINTENV': T / 'bin/fw_printenv',
    'EPILYKOS_FW_SETENV': T / 'bin/fw_setenv', 'EPILYKOS_DATA_DIR': T, 'EPILYKOS_OS_RETRY_S': '0',
    'EPILYKOS_FAKE': T / 'fake.json', 'EPILYKOS_RAUC_KEYRING': T / 'keyring.pem',
}.items():
    os.environ[name] = str(value)
sys.path.insert(0, str(Path(__file__).resolve().parent))
import os_trust_fixtures as fx  # noqa: E402
from os_trust_fixtures import trust  # noqa: E402
import epilykos_os_update as osu  # noqa: E402
import epilykos_app_compat as compat  # noqa: E402
import epilykos_storage as storage  # noqa: E402

FAKE_TOOL = textwrap.dedent('''\
    #!/usr/bin/env python3
    """Stand-ins for rauc, systemctl, busctl, podman, fw_printenv and fw_setenv."""
    import json, os, sys, time
    path = os.environ['EPILYKOS_FAKE']
    def load():
        with open(path) as f:
            return json.load(f)
    def store(state):
        with open(path + '.tmp', 'w') as f:
            json.dump(state, f)
        os.replace(path + '.tmp', path)
    tool, args = os.path.basename(sys.argv[0]), sys.argv[1:]
    state = load()
    state.setdefault('calls', []).append([tool, *args])
    store(state)
    env = state['env']
    if tool == 'fw_printenv':
        print(''.join(f'{k}={v}\\n' for k, v in env.items()), end='')
    elif tool == 'fw_setenv':
        if state.get('setenv_fails'):
            sys.exit(1)
        for line in sys.stdin.read().splitlines():
            name, _, value = line.partition(' ')
            if value:
                env[name] = value
            else:
                env.pop(name, None)
        store(state)
    elif tool == 'systemctl':
        if args[:2] == ['is-active', '--quiet']:
            sys.exit(0 if state.get('worker_active') else 3)
        if args[:2] == ['show', '--property=InvocationID']:
            print(state.get('invocation', 'inv-1'))
    elif tool == 'busctl':
        rauc = state.get('rauc_daemon')
        if not rauc:
            sys.exit(1)
        if 'GetNameOwner' in args:
            print(f's "{rauc["owner"]}"')
        elif args[-1] == 'Operation':
            ops = rauc.get('operations') or ['idle']
            print(f's "{ops[0]}"')
            if len(ops) > 1:
                rauc['operations'] = ops[1:]
                store(state)
        elif args[-1] == 'LastError':
            print(f's "{rauc.get("last_error", "")}"')
    elif tool == 'podman':
        print(json.dumps([state['container']]))
    elif tool == 'rauc':
        slot = lambda name: {'rootfs.0': 'A', 'rootfs.1': 'B'}.get(name) or \
            (state.get('booted', 'A') if name == 'booted' else ('B' if state.get('booted', 'A') == 'A' else 'A'))
        if args[:2] == ['status', 'mark-active'] and state.get('mark_active_works', True):
            chosen = slot(args[2])
            env['BOOT_ORDER'] = chosen + ' ' + ('B' if chosen == 'A' else 'A')
            env['BOOT_' + chosen + '_LEFT'] = '3'
            store(state)
            sys.exit(0)
        if args[:2] == ['status', 'mark-good']:
            env['BOOT_' + slot(args[2]) + '_LEFT'] = '3'
            store(state)
            sys.exit(0)
        if args[:2] == ['status', 'mark-bad']:
            if state.get('mark_bad_works', True):
                env['BOOT_' + slot(args[2]) + '_LEFT'] = '0'
                store(state)
            sys.exit(0)
        if args[0] == 'install':
            mode = state.get('install', 'ok')
            print('  0% Installing', flush=True)
            target = state['target_device']
            env['BOOT_' + state['target'] + '_LEFT'] = '0'
            store(state)
            if mode == 'hang':
                time.sleep(3600)
            print(' 40% Checking bundle done.', flush=True)
            if mode == 'fail':
                print('LastError: Installation error: failed to write', flush=True)
                sys.exit(1)
            image = open(state['image'], 'rb').read()
            if mode == 'corrupt':
                image = image[:-1] + bytes([image[-1] ^ 1])
            with open(target, 'r+b') as f:
                f.write(image)
            if mode == 'bootable':
                env['BOOT_' + state['target'] + '_LEFT'] = '3'
                store(state)
            print('100% Installing done.', flush=True)
''')


class Server(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        pass                    # clients cut off on purpose


class Handler(http.server.BaseHTTPRequestHandler):
    files = {}          # path -> bytes
    behaviour = {}      # see Feed.reset
    log = []

    def log_message(self, *args):
        pass

    def do_GET(self):
        b = self.behaviour
        data = self.files.get(self.path)
        Handler.log.append((self.path, dict(self.headers)))
        if data is None:
            self.send_error(404)
            return
        etag = b.get('etag', '"v1"')
        start = 0
        wanted = self.headers.get('Range')
        if wanted and not b.get('ignore_range') and self.headers.get('If-Range') == etag:
            start = int(wanted.split('=')[1].split('-')[0])
            self.send_response(206)
            first = b.get('wrong_range_start', start)
            self.send_header('Content-Range', f'bytes {first}-{len(data) - 1}/{len(data)}')
        else:
            self.send_response(200)
        body = (b.get('serve', data) if self.path.endswith('.raucb') else data)[start:]
        self.send_header('Content-Length', str(len(body)))
        if etag:
            self.send_header('ETag', etag)
        self.end_headers()
        cut = b.pop('cut_after', None) if self.path.endswith('.raucb') else None
        if cut is not None:
            self.wfile.write(body[:cut])
            self.wfile.flush()
            self.connection.shutdown(2)
            return
        delay = b.get('delay') if self.path.endswith('.raucb') else None
        for offset in range(0, len(body), 64 * 1024):
            self.wfile.write(body[offset:offset + 64 * 1024])
            if delay:
                time.sleep(delay)


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        (T / 'bin').mkdir(exist_ok=True)
        for tool in ('rauc', 'systemctl', 'busctl', 'podman', 'fw_printenv', 'fw_setenv'):
            (T / 'bin' / tool).write_text(FAKE_TOOL)
            (T / 'bin' / tool).chmod(0o755)
        t = cls.t = fx.Trust(T / 'keys')
        t.index_key('dev-1')
        t.ca('ca-1')
        t.leaf('leaf-1', 'ca-1')
        cls.p1 = t.policy(1, ['dev-1'], ['ca-1'], ['leaf-1'])
        cls.p2 = t.policy(2, ['dev-1'], ['ca-1'], ['leaf-1'])
        (T / 'os-index-root.pem').write_bytes(t.root_pem)
        cls.platform = fx.platform(sequence=11)
        (T / 'platform.json').write_bytes(trust.canonical(cls.platform))
        cls.image = os.urandom(3 * 1024 * 1024 + 12345)
        (T / 'image.ext4').write_bytes(cls.image)
        cls.bundle = cls.make_bundle(cls.image, sequence=12)
        cls.server = Server(('127.0.0.1', 0), Handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        base = f'http://127.0.0.1:{cls.server.server_address[1]}'

        def opener(url, headers=None):
            return osu.urllib.request.urlopen(osu.urllib.request.Request(url.replace(HOST, base), headers=headers or {}),
                                              timeout=10)
        osu._open = opener
        trust.FW_PRINTENV, trust.FW_SETENV = str(T / 'bin/fw_printenv'), str(T / 'bin/fw_setenv')
        trust.ROOT_PEM, trust.PLATFORM = str(T / 'os-index-root.pem'), str(T / 'platform.json')
        osu.OCI_ALLOWANCE = 0
        storage.usage = lambda path=None: (100 * storage.GIB, 50 * storage.GIB)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    @classmethod
    def make_bundle(cls, image, sequence, epoch=1, payload=None, **meta):
        manifest = '\n'.join([
            '[update]', 'compatible=epilykos-raspberrypi3', 'version=0.7', '', '[bundle]', 'format=verity', '',
            '[image.rootfs]', 'filename=rootfs.ext4', f'size={len(image)}',
            f'sha256={hashlib.sha256(image).hexdigest()}', '', '[meta.epilykos]',
            *(f'{k}={v}' for k, v in {'board': 'raspberrypi3', 'channel': 'dev', 'sequence': sequence,
                                      'boot_protocol': 2, 'min_host_api': 2, 'trust_epoch': epoch, **meta}.items()),
            ''])
        (T / 'manifest.raucm').write_text(manifest)
        key, cert, _ = cls.t.leaves['leaf-1']
        cms = subprocess.run(['openssl', 'cms', '-sign', '-binary', '-nodetach', '-in', T / 'manifest.raucm',
                              '-signer', cert, '-inkey', key, '-outform', 'DER'], check=True,
                             capture_output=True).stdout
        payload = payload if payload is not None else os.urandom(2 * 1024 * 1024 + 777)
        return payload + cms + len(cms).to_bytes(8, 'big')

    def publish(self, bundle=None, sequence=12, policy=None, **entry):
        bundle = self.bundle if bundle is None else bundle
        entry = {'bundle': {'url': f'{HOST}/os-dev/epilykos-os-0.7-raspberrypi3.raucb', 'size': len(bundle),
                            'sha256': hashlib.sha256(bundle).hexdigest()}, 'version': '0.7', **entry}
        epoch = entry.pop('epoch', 1)
        raw, sig = self.t.entry('dev-1', sequence=sequence, epoch=epoch, **entry)
        policy = policy or self.p1
        Handler.files = {'/os-dev/os-index-dev.json': raw, '/os-dev/os-index-dev.json.sig': sig,
                         '/os-dev/os-policy.json': policy[0], '/os-dev/os-policy.sig': policy[1],
                         '/os-dev/epilykos-os-0.7-raspberrypi3.raucb': bundle}
        return json.loads(raw)

    def setUp(self):
        shutil.rmtree(T / 'os-updater', ignore_errors=True)
        (T / 'slot-a').write_bytes(b'\0' * 64)
        (T / 'slot-b').write_bytes(b'\0' * (len(self.image) + 4096))
        (T / 'maintenance.lock').touch()
        (T / 'cmdline').write_text('root=PARTUUID=45504c4b-02 rauc.slot=A ro\n')
        (T / 'os-health.json').write_text(json.dumps({'schema': 1, 'state': 'confirmed', 'slot': 'A'}))
        Handler.behaviour, Handler.log = {}, []
        env = fx.env_for(*self.p1, 1, floor=11)
        env.update({'BOOT_ORDER': 'A B', 'BOOT_A_LEFT': '3', 'BOOT_B_LEFT': '3'})
        self.fake({'env': env, 'container': self.container(), 'rauc_daemon': {'owner': ':1.7'},
                   'invocation': 'inv-1', 'target': 'B', 'target_device': str(T / 'slot-b'),
                   'image': str(T / 'image.ext4')})
        self.publish()
        osu.reconcile()         # what hostd does at start: an idle guard

    def fake(self, state=None, **update):
        if state is None:
            state = json.loads((T / 'fake.json').read_text())
        state.update(update)
        (T / 'fake.json').write_text(json.dumps(state))
        return state

    def state(self):
        return json.loads((T / 'fake.json').read_text())

    def calls(self, tool):
        return [c[1:] for c in self.state().get('calls', []) if c[0] == tool]

    @staticmethod
    def container(guard=True):
        labels = {'io.epilykos.maintenance-lock': 'flock-v1'}
        env = ['EPILYKOS_MAINTENANCE_LOCK=/app/data/.maintenance.lock']
        mounts = [{'Destination': '/app/data', 'Type': 'bind', 'Source': '/data/epilykos/app-data', 'RW': True}]
        if guard:
            labels['io.epilykos.os-update-guard'] = 'guard-v1'
            env.append('EPILYKOS_OS_UPDATE_GUARD=/run/epilykos-os-updater/guard.json')
            mounts.append({'Destination': '/run/epilykos-os-updater', 'Type': 'bind',
                           'Source': '/data/epilykos/os-updater/public', 'RW': False})
        return {'Config': {'Labels': labels, 'Env': env}, 'State': {'Running': True}, 'Mounts': mounts}

    def admit(self, request='req-00000001'):
        candidate = osu.check()['os']['candidate']
        osu.install(candidate['sequence'], candidate['sha256'], request)
        return osu.journal()

    def run_job(self):
        code = osu.run_worker()
        return code, osu.journal()


class AdmissionTests(Base):
    def test_check_offers_a_newer_verified_package(self):
        result = osu.check()['os']
        self.assertTrue(result['available'])
        self.assertEqual(result['blockers'], [])
        self.assertEqual((result['candidate']['sequence'], result['candidate']['version']), (12, '0.7'))
        self.assertFalse(result['trust_update'])

    def test_not_newer_than_floor_or_running_slot_is_not_offered(self):
        self.publish(sequence=11)
        self.assertFalse(osu.check()['os']['available'])

    def test_blockers_unhealthy_slot_pending_job_and_old_app(self):
        (T / 'os-health.json').unlink()
        env = self.state()['env']
        env['epk_os_pending'] = '11111111-2222-4333-8444-555555555555'
        self.fake(env=env, container=self.container(guard=False))
        blockers = ' '.join(osu.check()['os']['blockers'])
        self.assertIn('not been confirmed healthy', blockers)
        self.assertIn('earlier OS update', blockers)
        self.assertIn('cannot take part in OS updates', blockers)
        with self.assertRaises(osu.OsUpdateError):
            osu.install(12, hashlib.sha256(self.bundle).hexdigest(), 'req-00000001')

    def test_not_enough_space_is_refused_without_deleting_anything(self):
        storage.usage = lambda path=None: (100 * storage.GIB, 10 * storage.GIB + len(self.bundle) - 1)
        try:
            self.assertIn('Not enough free space', ' '.join(osu.check()['os']['blockers']))
            with self.assertRaises(storage.StorageError):
                osu.install(12, hashlib.sha256(self.bundle).hexdigest(), 'req-00000001')
        finally:
            storage.usage = lambda path=None: (100 * storage.GIB, 50 * storage.GIB)

    def test_install_is_exact_and_idempotent_by_request_id(self):
        job = self.admit('req-00000001')
        self.assertEqual((job['state'], job['sequence'], job['source'], job['target']), ('queued', 12, 'A', 'B'))
        self.assertIn(['start', '--no-block', 'epilykos-os-update.service'], self.calls('systemctl'))
        osu.install(12, job['sha256'], 'req-00000001')
        self.assertEqual(osu.journal()['id'], job['id'])
        with self.assertRaises(osu.OsUpdateError):
            osu.install(12, job['sha256'], 'req-00000002')
        with self.assertRaises(osu.OsUpdateError):
            osu.install(12, 'f' * 64, 'req-00000001')
        self.assertEqual(osu.blocking(), 'active', 'a queued job blocks maintenance before the worker starts')

    def test_install_needs_the_exact_checked_package(self):
        osu.check()
        with self.assertRaisesRegex(osu.OsUpdateError, 'changed'):
            osu.install(12, 'e' * 64, 'req-00000001')
        for bad in ((12.0, 'a' * 64, 'req-00000001'), (12, 'a' * 64, 'x'), (12, 'A' * 64, 'req-00000001')):
            with self.assertRaises(osu.OsUpdateError):
                osu.install(*bad)

    def test_protocol1_cards_have_no_os_updates_and_nothing_blocked(self):
        (T / 'platform.json').rename(T / 'platform.saved')
        try:
            view = osu.view()
            self.assertFalse(view['supported'])
            self.assertIn('Flash a current EpilykosOS image once', view['reason'])
            self.assertEqual(osu.blocking(), 'idle')
        finally:
            (T / 'platform.saved').rename(T / 'platform.json')


class WorkerTests(Base):
    def test_download_verify_install_ready(self):
        job = self.admit()
        code, job = self.run_job()
        self.assertEqual((code, job['state'], job['error']), (0, 'ready', None))
        self.assertEqual((T / 'slot-b').read_bytes()[:len(self.image)], self.image)
        self.assertEqual(osu.guard(), {'schema': 1, 'state': 'pending', 'job': job['id']})
        install = [c for c in self.calls('rauc') if c[0] == 'install'][0]
        self.assertEqual(install[1], f'--transaction-id={job["id"]}')
        self.assertEqual(job['rauc'], {'transaction': job['id'], 'bus_owner': ':1.7', 'invocation': 'inv-1'})
        self.assertTrue((osu.STAGING / f'{job["id"]}.raucb').exists(), 'retained until the job ends')
        self.assertEqual(self.state()['env']['BOOT_ORDER'], 'A B', 'installing never changes the boot target')
        fd = os.open(T / 'maintenance.lock', os.O_RDWR)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)     # the lease was released
        os.close(fd)
        self.assertEqual(osu.public(job)['state'], 'ready')
        self.assertNotIn('entry', osu.public(job))

    def test_interrupted_download_resumes_with_range_and_validator(self):
        Handler.behaviour = {'cut_after': 1024 * 1024 + 5}
        self.admit()
        code, job = self.run_job()
        self.assertEqual((code, job['state']), (0, 'ready'))
        gets = [h for p, h in Handler.log if p.endswith('.raucb')]
        self.assertEqual(len(gets), 2)
        self.assertNotIn('Range', gets[0])
        self.assertTrue(gets[1]['Range'].startswith('bytes='))
        self.assertEqual(gets[1]['If-Range'], '"v1"')

    def test_ignored_range_or_changed_validator_restarts_from_zero(self):
        for behaviour in ({'cut_after': 900000, 'ignore_range': True}, {'cut_after': 900000, 'etag': None}):
            with self.subTest(behaviour=behaviour):
                self.setUp()
                Handler.behaviour = dict(behaviour)
                self.admit()
                code, job = self.run_job()
                self.assertEqual((code, job['state']), (0, 'ready'))
                gets = [h for p, h in Handler.log if p.endswith('.raucb')]
                if behaviour.get('etag', '"v1"') is None:
                    self.assertNotIn('Range', gets[1], 'no validator: never resume')

    def test_wrong_content_range_is_not_appended(self):
        Handler.behaviour = {'cut_after': 700000, 'wrong_range_start': 0}
        self.admit()
        code, job = self.run_job()
        self.assertEqual((code, job['state']), (0, 'ready'))

    def test_tampered_or_oversized_download_never_reaches_rauc(self):
        for name, serve in (('tampered', self.bundle[:-100] + b'x' * 100), ('oversized', self.bundle + b'extra')):
            with self.subTest(name):
                self.setUp()
                Handler.behaviour = {'serve': serve}
                self.admit()
                code, job = self.run_job()
                self.assertEqual((code, job['state']), (1, 'failed'))
                self.assertFalse([c for c in self.calls('rauc') if c[0] == 'install'])
                self.assertEqual(osu.guard()['state'], 'idle')
                self.assertEqual(list(osu.STAGING.iterdir()), [])

    def test_package_signed_for_another_sequence_is_refused(self):
        bundle = self.make_bundle(self.image, sequence=13)
        self.publish(bundle=bundle, sequence=12)
        self.admit()
        code, job = self.run_job()
        self.assertEqual(job['state'], 'failed')
        self.assertIn('disagree on sequence', job['error'])

    def test_data_filling_up_stops_the_download(self):
        Handler.behaviour = {'delay': 0.05}
        self.admit()
        original, osu.time.monotonic = osu.time.monotonic, (lambda c=[0]: c.__setitem__(0, c[0] + 3) or c[0])
        storage.usage = lambda path=None: (100 * storage.GIB, 5 * storage.GIB)
        try:
            code, job = self.run_job()
        finally:
            osu.time.monotonic = original
            storage.usage = lambda path=None: (100 * storage.GIB, 50 * storage.GIB)
        self.assertEqual(job['state'], 'failed')
        self.assertIn('nearly full', job['error'])

    def test_cancel_during_download(self):
        Handler.behaviour = {'delay': 0.2}
        job = self.admit()
        worker = threading.Thread(target=osu.run_worker)
        worker.start()
        for _ in range(100):
            if osu.journal()['state'] == 'downloading':
                break
            time.sleep(0.05)
        self.fake(worker_active=True)
        osu.cancel(job['id'])
        worker.join(30)
        job = osu.journal()
        self.assertEqual(job['state'], 'cancelled')
        self.assertEqual(osu.guard()['state'], 'idle')
        self.assertEqual(list(osu.STAGING.iterdir()), [])
        self.fake(worker_active=False)

    def test_no_cancel_once_installing(self):
        job = self.admit()
        job['state'] = 'installing'
        osu.save(job)
        with self.assertRaisesRegex(osu.OsUpdateError, 'can no longer be cancelled'):
            osu.cancel(job['id'])

    def test_rauc_failure_or_wrong_contents_leave_the_slot_bad(self):
        for mode, expect in (('fail', 'refused or failed'), ('corrupt', 'does not match the signed image'),
                             ('bootable', 'unexpectedly bootable')):
            with self.subTest(mode=mode):
                self.setUp()
                self.fake(install=mode)
                self.admit()
                code, job = self.run_job()
                self.assertEqual(job['state'], 'failed')
                self.assertIn(expect, job['error'])
                self.assertIn(['status', 'mark-bad', 'rootfs.1'], self.calls('rauc'))
                self.assertEqual(self.state()['env']['BOOT_B_LEFT'], '0')
                self.assertEqual(osu.guard()['state'], 'idle')

    def test_unprovable_slot_state_is_reconciling_not_guessed(self):
        self.fake(install='bootable', mark_bad_works=False)
        self.admit()
        code, job = self.run_job()
        self.assertEqual(job['state'], 'reconciling')
        self.assertEqual(osu.guard()['state'], 'reconciling')
        self.assertEqual(osu.blocking(), 'reconciling')

    def test_app_maintenance_holding_the_lock_fails_the_job_early(self):
        self.admit()
        fd = os.open(T / 'maintenance.lock', os.O_RDWR)
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            code, job = self.run_job()
        finally:
            os.close(fd)
        self.assertEqual(job['state'], 'failed')
        self.assertIn('maintenance', job['error'])

    def test_newer_trust_policy_is_admitted_first_and_kept(self):
        bundle = self.make_bundle(self.image, sequence=12, epoch=2)
        self.publish(bundle=bundle, policy=self.p2, epoch=2)
        self.fake(install='fail')
        self.assertTrue(osu.check()['os']['trust_update'])
        self.admit()
        code, job = self.run_job()
        self.assertEqual(job['state'], 'failed')
        env = self.state()['env']
        self.assertEqual(env['epk_trust_epoch'], '2', 'trust advancement survives the failed candidate')
        self.assertEqual(base64.b64decode(env['epk_trust_policy']), self.p2[0])
        self.assertIn(['restart', 'rauc.service'], self.calls('systemctl'))
        self.assertEqual(env['epk_os_floor'], '11')


class InterruptionTests(Base):
    def installing_job(self):
        self.admit()
        job = osu.journal()
        job['image'] = {'size': len(self.image), 'sha256': hashlib.sha256(self.image).hexdigest()}
        job['rauc'] = {'transaction': job['id'], 'bus_owner': ':1.7', 'invocation': 'inv-1'}
        job['state'] = 'installing'
        osu.save(job)
        osu.set_guard('active', job['id'])
        return job

    def test_recorded_rauc_finishing_alone_completes_the_job(self):
        self.installing_job()
        (T / 'slot-b').write_bytes(self.image)
        env = self.state()['env']
        env['BOOT_B_LEFT'] = '0'
        self.fake(env=env, rauc_daemon={'owner': ':1.7', 'operations': ['installing', 'installing', 'idle']})
        osu.time.sleep, sleep = (lambda s: None), osu.time.sleep
        try:
            code, job = self.run_job()
        finally:
            osu.time.sleep = sleep
        self.assertEqual(job['state'], 'ready')
        self.assertEqual(osu.guard()['state'], 'pending')

    def test_rauc_error_after_the_worker_died_fails_and_marks_bad(self):
        self.installing_job()
        self.fake(rauc_daemon={'owner': ':1.7', 'last_error': 'write failed'})
        code, job = self.run_job()
        self.assertEqual(job['state'], 'failed')
        self.assertIn(['status', 'mark-bad', 'rootfs.1'], self.calls('rauc'))

    def test_a_different_rauc_service_means_interrupted(self):
        for change in ({'rauc_daemon': {'owner': ':1.99'}}, {'invocation': 'inv-2'}, {'rauc_daemon': None}):
            with self.subTest(change=change):
                self.setUp()
                self.installing_job()
                (T / 'slot-b').write_bytes(self.image)       # even correct-looking contents
                self.fake(**change)
                code, job = self.run_job()
                self.assertEqual(job['state'], 'failed')
                self.assertIn('interrupted', job['error'])
                self.assertEqual(osu.guard()['state'], 'idle')

    def test_hostd_start_rebuilds_the_guard_and_resumes(self):
        job = self.admit()
        (osu.PUBLIC / 'guard.json').unlink(missing_ok=True)
        self.assertEqual(osu.guard()['state'], 'reconciling', 'missing guard fails closed')
        osu.reconcile()
        self.assertEqual(osu.guard(), {'schema': 1, 'state': 'active', 'job': job['id']})
        self.assertEqual(self.calls('systemctl').count(['start', '--no-block', 'epilykos-os-update.service']), 2)
        job['state'] = 'ready'
        osu.save(job)
        osu.reconcile()
        self.assertEqual(osu.guard()['state'], 'pending')
        job['state'] = 'failed'
        osu.save(job)
        osu.reconcile()
        self.assertEqual(osu.guard()['state'], 'idle')

    def test_killed_worker_resumes_its_download(self):
        Handler.behaviour = {'delay': 0.05}
        self.admit()
        env = dict(os.environ, PYTHONPATH=str(OVERLAY / 'usr/lib/epilykos/python'))
        script = textwrap.dedent(f'''
            import epilykos_os_update as osu, epilykos_storage as storage
            base = {f"http://127.0.0.1:{self.server.server_address[1]}"!r}
            osu._open = lambda url, headers=None: osu.urllib.request.urlopen(
                osu.urllib.request.Request(url.replace({HOST!r}, base), headers=headers or {{}}), timeout=10)
            osu.OCI_ALLOWANCE = 0
            storage.usage = lambda path=None: (100 * storage.GIB, 50 * storage.GIB)
            osu.run_worker()
        ''')
        worker = subprocess.Popen([sys.executable, '-c', script], env=env, cwd=str(T))
        part = None
        for _ in range(200):
            parts = list(osu.STAGING.glob('*.part')) if osu.STAGING.exists() else []
            if parts and parts[0].stat().st_size > 512 * 1024:
                part = parts[0]
                break
            time.sleep(0.05)
        worker.send_signal(signal.SIGKILL)
        worker.wait()
        self.assertIsNotNone(part)
        kept = part.stat().st_size
        self.assertGreater(kept, 0)
        self.assertEqual(osu.journal()['state'], 'downloading')
        Handler.behaviour = {}
        Handler.log = []
        code, job = self.run_job()
        self.assertEqual(job['state'], 'ready')
        gets = [h for p, h in Handler.log if p.endswith('.raucb')]
        self.assertTrue(gets[0]['Range'].startswith('bytes='), 'the retained part is resumed')


class GuardTests(Base):
    def load_hostd(self):
        loader = importlib.machinery.SourceFileLoader('hostd_under_test', str(OVERLAY / 'usr/bin/epilykos-hostd'))
        spec = importlib.util.spec_from_loader('hostd_under_test', loader)
        module = importlib.util.module_from_spec(spec)
        loader.exec_module(module)
        module.later = lambda fn, delay=1.0: None
        return module

    def test_hostd_refuses_conflicting_maintenance_by_guard_state(self):
        hostd = self.load_hostd()
        osu._layout()
        cases = {'active': {'restart', 'shutdown', 'factory_reset', 'set_port', 'update_apply'},
                 'pending': {'restart', 'shutdown', 'update_apply'},
                 'reconciling': {'restart', 'shutdown', 'factory_reset', 'set_port', 'update_apply'}}
        requests = {'restart': {}, 'shutdown': {}, 'factory_reset': {'confirm': 'ERASE'}, 'set_port': {'port': 8080},
                    'update_apply': {'target': 'app'}}
        for state, refused in cases.items():
            osu.set_guard(state, '11111111-2222-4333-8444-555555555555')
            for action in refused:
                with self.subTest(state=state, action=action), self.assertRaises(osu.OsUpdateError):
                    hostd.handle({'action': action, **requests[action]})
        osu.set_guard('pending', '11111111-2222-4333-8444-555555555555')
        osu.refuse_if('active', 'reconciling', action='A factory reset')     # allowed while ready
        osu.set_guard('idle')
        self.assertEqual(osu.blocking(), 'idle')

    def test_protocol1_card_gets_an_idle_guard_at_boot(self):
        """A protocol-1 card has no OS update jobs, but the app is still told
        where the guard is and fails closed on a missing file, which held back
        its retention, backup and integrity jobs for good."""
        hostd = self.load_hostd()
        (osu.PUBLIC / 'guard.json').unlink()
        self.assertEqual(osu.guard()['state'], 'reconciling')
        with unittest.mock.patch.object(osu, 'platform', lambda: None):
            hostd.reconcile_os_update()
        self.assertEqual(json.loads((osu.PUBLIC / 'guard.json').read_text())['state'], 'idle')

    def test_guard_is_public_and_job_state_private(self):
        self.admit()
        self.run_job()
        self.assertEqual(oct(osu.PRIVATE.stat().st_mode & 0o777), '0o700')
        self.assertEqual(oct((osu.PRIVATE / 'journal.json').stat().st_mode & 0o777), '0o600')
        self.assertEqual(oct((osu.PUBLIC / 'guard.json').stat().st_mode & 0o777), '0o644')
        self.assertEqual(sorted(json.loads((osu.PUBLIC / 'guard.json').read_text())), ['job', 'schema', 'state'])

    def test_app_guard_compatibility(self):
        compat.os_update_guard(self.container())
        broken = [('no label', lambda c: c['Config']['Labels'].pop('io.epilykos.os-update-guard')),
                  ('rw mount', lambda c: c['Mounts'][1].__setitem__('RW', True)),
                  ('other source', lambda c: c['Mounts'][1].__setitem__('Source', '/data')),
                  ('no env', lambda c: c['Config']['Env'].pop())]
        for name, damage in broken:
            container = self.container()
            damage(container)
            with self.subTest(name), self.assertRaises(compat.CompatibilityError):
                compat.os_update_guard(container)

    def test_image_wiring(self):
        run = (OVERLAY / 'usr/bin/epilykos-run').read_text()
        self.assertIn('--volume /data/epilykos/os-updater/public:/run/epilykos-os-updater:ro', run)
        self.assertIn('--env EPILYKOS_OS_UPDATE_GUARD=/run/epilykos-os-updater/guard.json', run)
        unit = (OVERLAY / 'usr/lib/systemd/system/epilykos-os-update.service').read_text()
        self.assertIn('ExecStart=/usr/bin/epilykos-os-update', unit)
        self.assertNotIn('[Install]', unit, 'started only by hostd')
        tmpfiles = (OVERLAY / 'usr/lib/tmpfiles.d/epilykos.conf').read_text()
        self.assertIn('d /data/epilykos/os-updater/private  0700 root root -', tmpfiles)


if __name__ == '__main__':
    try:
        unittest.main()
    finally:
        shutil.rmtree(T, ignore_errors=True)
