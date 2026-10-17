#!/usr/bin/env python3
"""Self-tests for application updates from Epilykos settings (epilykos_update,
C-UPDATE-002/003/004, D-UPDATE-001), with real Ed25519 signatures from a
throwaway key, a local feed, and stand-ins for podman and systemctl:

- only a manifest signed with the pinned key, for this channel, with a
  sequence above the floor, is offered or installed
- a healthy candidate is promoted (floor raised, previous kept); an
  unhealthy one is rolled back without raising the floor
- an update cut short is rolled back at the next hostd start
- epilykos-run starts the promoted image only when it is at least as new
  as the slot's preloaded one
- hostd answers update_status / update_check / update_apply on its socket
"""
import json
import fcntl
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent.parent
OVERLAY = ROOT / 'os/board/epilykos/raspberrypi3/rootfs-overlay'
PYLIB = OVERLAY / 'usr/lib/epilykos/python'

T = Path(tempfile.mkdtemp(prefix='epilykos-update-'))
BASE_ID = 'b' * 64
DIG1, DIG2, DIG3 = ('sha256:' + c * 64 for c in '123')
ID_FOR = {DIG1: '1' * 64, DIG2: '2' * 64, DIG3: '3' * 64}


def ok(name):
    print(f'ok - {name}')


def openssl(*args):
    subprocess.run(['openssl', *args], check=True, capture_output=True)


# Keys: the pinned one and a stranger's.
for name in ('release', 'other'):
    openssl('genpkey', '-algorithm', 'ed25519', '-out', str(T / f'{name}.key'))
    openssl('pkey', '-in', str(T / f'{name}.key'), '-pubout', '-out', str(T / f'{name}.pub'))

(T / 'image.env').write_text(
    'EPILYKOS_CHANNEL=dev\nEPILYKOS_RELEASE=2.7.0-dev+aaaa\nEPILYKOS_SEQUENCE=7\n'
    f'EPILYKOS_IMAGE_REF=docker.io/irunmole/epilykos@sha256:{"0" * 64}\nEPILYKOS_IMAGE_ID={BASE_ID}\nEPILYKOS_OS_VERSION=abc1234\n')
feed = T / 'feed'
feed.mkdir()
state = T / 'updater'
calls = T / 'calls'
restart_rc = T / 'restart_rc'   # one exit code per line, consumed in order

(T / 'podman').write_text(f'''#!/bin/sh
echo "podman $*" >> {calls}
case "$1" in
  pull) if flock -n {T}/maintenance.lock -c true; then echo "maintenance lock missing" >&2; exit 125; fi
        [ -f {T}/pull_fails ] && {{ echo "Error: initializing source: manifest unknown" >&2; exit 125; }}; exit 0 ;;
  image) case "$2" in
    inspect) case "$5" in
      *{DIG1}) echo {ID_FOR[DIG1]} ;; *{DIG2}) echo {ID_FOR[DIG2]} ;; *{DIG3}) echo {ID_FOR[DIG3]} ;; esac ;;
    exists) exit 0 ;;
  esac ;;
esac
exit 0
''')
(T / 'systemctl').write_text(f'''#!/bin/sh
echo "systemctl $* active=$(sed -n 's/^EPILYKOS_ACTIVE_IMAGE_ID=//p' {state}/active.env 2>/dev/null)" >> {calls}
if flock -n {T}/maintenance.lock -c true; then echo "maintenance lock missing" >&2; exit 125; fi
rc=$(head -n1 {restart_rc} 2>/dev/null); sed -i 1d {restart_rc} 2>/dev/null
exit ${{rc:-0}}
''')
for f in ('podman', 'systemctl'):
    os.chmod(T / f, 0o755)

os.environ.update(EPILYKOS_MAINTENANCE_LOCK=str(T / 'maintenance.lock'), EPILYKOS_UPDATE_DIR=str(state), EPILYKOS_IMAGE_ENV=str(T / 'image.env'),
                  EPILYKOS_APP_PUBKEY=str(T / 'missing.pub'), EPILYKOS_UPDATE_URL=f'file://{feed}',
                  EPILYKOS_PODMAN=str(T / 'podman'), EPILYKOS_SYSTEMCTL=str(T / 'systemctl'))
sys.path.insert(0, str(PYLIB))
import epilykos_update as up  # noqa: E402


def publish(seq, digest, release, channel='dev', key='release', tamper=False):
    m = {'schema': 1, 'release': release, 'channel': channel, 'sequence': seq,
         'images': {'epilykos': {'digest': digest, 'source': 'irunmole/epilykos'}}}
    body = json.dumps(m, sort_keys=True, separators=(',', ':')).encode()
    (feed / 'manifest.json').write_bytes(body)
    subprocess.run(['openssl', 'pkeyutl', '-sign', '-inkey', str(T / f'{key}.key'), '-rawin',
                    '-in', str(feed / 'manifest.json'), '-out', str(feed / 'manifest.json.sig')], check=True)
    if tamper:
        (feed / 'manifest.json').write_bytes(body.replace(digest.encode(), DIG3.encode()))


def wait_idle():
    for _ in range(200):
        if not up._running.is_set():
            assert subprocess.run(['flock', '-n', str(T / 'maintenance.lock'), '-c', 'true']).returncode == 0, 'maintenance released after job ends'
            return
        time.sleep(0.02)
    raise AssertionError('update did not finish')


def read(name):
    return json.loads((state / name).read_text())


# -- checking -----------------------------------------------------------------------
publish(8, DIG1, '2.8.0-dev+bbbb')
r = up.check()
assert r['ok'] and r['app']['available'] is False and 'no app update key' in r['app']['error'], r
assert r['os'] == {'version': 'abc1234', 'available': False}
up.PUBKEY = str(T / 'release.pub')
r = up.check()['app']
assert r['available'] is True and r['latest_version'] == '2.8.0-dev+bbbb' and r['latest_digest'] == DIG1, r
assert r['current_version'] == '2.7.0-dev+aaaa' and r['state'] == 'idle'
ok('check: no pinned key, no updates; a signed newer manifest is offered')

for kwargs, why in [({'tamper': True}, 'not signed'), ({'key': 'other'}, 'not signed'), ({'channel': 'stable'}, 'stable channel')]:
    publish(8, DIG1, '2.8.0-dev+bbbb', **kwargs)
    r = up.check()['app']
    assert r['available'] is False and why in r['error'], (kwargs, r)
publish(7, DIG1, '2.7.1-dev+cccc')
assert up.check()['app']['available'] is False, 'sequence at the floor is not newer'
up.FEED = f'file://{T}/nowhere'
assert 'Could not reach' in up.check()['app']['error']
up.FEED = f'file://{feed}'
ok('check: tampered, wrong key, wrong channel, replayed sequence and no network are all refused')

# -- installing ---------------------------------------------------------------------
try:
    up.apply('os')
    raise AssertionError('os target')
except up.UpdateError as e:
    assert 'not published yet' in str(e)

publish(8, DIG1, '2.8.0-dev+bbbb')
up.check()
calls.write_text('')
# A backup/restore in another process must exclude update allocation/restart.
with open(T / 'maintenance.lock', 'a+') as held:
    fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        up.apply('app')
    except up.UpdateError as e:
        assert 'maintenance' in str(e).lower(), e
    else:
        raise AssertionError('update started while app maintenance owned the shared lock')
    assert not (state / 'job.json').exists()
    assert not calls.read_text(), 'refused update must not pull or restart'
ok('apply: shared app maintenance refuses an update before allocating or restarting')
r = up.apply('app')
assert r == {'ok': True, 'state': 'applying', 'message': 'Downloading the update'}, r
wait_idle()
log = calls.read_text().splitlines()
assert f'podman pull --quiet docker.io/irunmole/epilykos@{DIG1}' in log, log
assert f'systemctl restart epilykos.service active={ID_FOR[DIG1]}' in log, log
assert read('result.json')['ok'] is True
assert read('current.json')['image_id'] == ID_FOR[DIG1] and (state / 'floor').read_text().strip() == '8'
assert not (state / 'previous.json').exists(), 'the preloaded image is the fallback, not a file'
assert up.status()['app']['current_version'] == '2.8.0-dev+bbbb' and up.status()['app']['state'] == 'idle'
assert oct((state).stat().st_mode & 0o777) == '0o700'
ok('apply: pulls by digest, starts it, promotes once healthy (floor 8)')

# The next one does not come up healthy: back to 2.8.0, floor unchanged.
publish(9, DIG2, '2.9.0-dev+dddd')
restart_rc.write_text('1\n0\n')
calls.write_text('')
assert up.check()['app']['auto_install_supported']
try:
    up.apply(automatic=True, sequence=999, digest=DIG2)
except up.UpdateError: pass
else: raise AssertionError('changed verified release accepted')
assert up.apply(automatic=True, sequence=9, digest=DIG2)['state'] == 'applying'
wait_idle()
assert up.apply(automatic=True, sequence=9, digest=DIG2)['state'] == 'already-attempted'
log = calls.read_text().splitlines()
assert log[-2:] == [f'systemctl restart epilykos.service active={ID_FOR[DIG2]}',
                    f'systemctl restart epilykos.service active={ID_FOR[DIG1]}'], log
res = read('result.json')
assert res['ok'] is False and res['rolled_back'] is True and 'did not start' in res['error'], res
assert read('current.json')['image_id'] == ID_FOR[DIG1] and (state / 'floor').read_text().strip() == '8'
ok('apply: an unhealthy candidate is rolled back; the floor is not raised')

# A download failure never stops the running app.
(T / 'pull_fails').write_text('')
calls.write_text('')
up.apply()
wait_idle()
assert 'manifest unknown' in read('result.json')['error'] and not any('restart' in l for l in calls.read_text().splitlines())
(T / 'pull_fails').unlink()
ok('apply: a failed download leaves the running app alone')

# Success again; then a replay of the old 2.8.0 manifest is refused.
up.apply()
wait_idle()
assert read('current.json')['sequence'] == 9 and read('previous.json')['sequence'] == 8
publish(8, DIG1, '2.8.0-dev+bbbb')
assert up.apply() == {'ok': True, 'state': 'current'}
ok('apply: 2.9.0 promoted with 2.8.0 as previous-known-good; replaying 2.8.0 is refused')

# Cut short (power loss mid-start): the next hostd start goes back.
(state / 'job.json').write_text(json.dumps({'state': 'starting', 'release': '3.0.0', 'sequence': 10}))
(state / 'active.env').write_text(f'EPILYKOS_ACTIVE_IMAGE_ID={ID_FOR[DIG3]}\nEPILYKOS_ACTIVE_SEQUENCE=10\n')
up.recover()
assert f'EPILYKOS_ACTIVE_IMAGE_ID={ID_FOR[DIG2]}' in (state / 'active.env').read_text()
assert read('result.json')['error'].startswith('The update was interrupted') and not (state / 'job.json').exists()
ok('recover: an interrupted update goes back to the last promoted image')

# -- what CI publishes is what appliances accept ---------------------------------------
signed = T / 'signed'
r = subprocess.run([sys.executable, str(ROOT / 'tools/sign_app_manifest.py'), str(ROOT / 'manifests/dev.yaml'), str(signed)],
                   env=dict(os.environ, APP_MANIFEST_SIGNING_KEY_FILE=str(T / 'release.key'), APP_MANIFEST_PUBKEY=str(T / 'release.pub')),
                   capture_output=True, text=True)
assert r.returncode == 0, r.stderr
m = up.verify((signed / 'manifest.json').read_bytes(), (signed / 'manifest.json.sig').read_bytes())
assert m['channel'] == 'dev' and m['digest'].startswith('sha256:') and m['source'] == 'irunmole/epilykos', m
r = subprocess.run([sys.executable, str(ROOT / 'tools/sign_app_manifest.py'), str(ROOT / 'manifests/dev.yaml'), str(T / 'signed2')],
                   env=dict(os.environ, APP_MANIFEST_SIGNING_KEY_FILE=str(T / 'other.key'), APP_MANIFEST_PUBKEY=str(T / 'release.pub')),
                   capture_output=True, text=True)
assert r.returncode != 0, 'signing with a key that does not match the pinned one fails in CI'
ok('sign_app_manifest: the merged manifest, signed by CI, verifies on the appliance; a wrong key fails in CI')

# -- epilykos-run picks the image ----------------------------------------------------
run = (OVERLAY / 'usr/bin/epilykos-run').read_text()
run = run.replace('/usr/lib/epilykos/image.env', str(T / 'image.env')).replace('/data/epilykos/updater/active.env', str(state / 'active.env')).replace('/usr/lib/epilykos/python', str(PYLIB))
(T / 'run.sh').write_text(run)
bin_dir = T / 'bin'
bin_dir.mkdir()
(bin_dir / 'podman').write_text(f'#!/bin/sh\n[ "$1" = image ] && exit 0\nfor arg; do echo "$arg"; done > {T}/run-args\necho "$@" | awk \'{{print $NF}}\' > {T}/started\n')
os.chmod(bin_dir / 'podman', 0o755)


def started():
    subprocess.run(['sh', str(T / 'run.sh')], check=True, env=dict(os.environ, PATH=f'{bin_dir}:{os.environ["PATH"]}'))
    return (T / 'started').read_text().strip()


assert started() == ID_FOR[DIG2]
run_args = (T / 'run-args').read_text().splitlines()
i = run_args.index('EPILYKOS_MAINTENANCE_LOCK=/app/data/.maintenance.lock')
assert run_args[i - 1] == '--env', 'OS must opt the app into the shared lock'
(state / 'active.env').write_text(f'EPILYKOS_ACTIVE_IMAGE_ID={ID_FOR[DIG1]}\nEPILYKOS_ACTIVE_SEQUENCE=6\n')
assert started() == BASE_ID, 'an OS slot with a newer preloaded image wins'
(state / 'active.env').write_text('EPILYKOS_ACTIVE_IMAGE_ID=$(reboot)\nEPILYKOS_ACTIVE_SEQUENCE=99\n')
assert started() == BASE_ID, 'active.env is parsed, never run'
ok('epilykos-run: starts the promoted image only when it is at least as new as the preloaded one')

# -- over hostd's socket -------------------------------------------------------------
sock = T / 'host.sock'
env = dict(os.environ, EPILYKOS_HOST_SOCKET=str(sock), EPILYKOS_RUN_DIR=str(T / 'run'), EPILYKOS_SSH_DIR=str(T / 'ssh'),
           EPILYKOS_APP_GID=str(os.getgid()), EPILYKOS_APP_PUBKEY=str(T / 'release.pub'), PYTHONPATH=str(PYLIB),
           PYTHONDONTWRITEBYTECODE='1')
proc = subprocess.Popen([sys.executable, str(OVERLAY / 'usr/bin/epilykos-hostd')], env=env,
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
try:
    for _ in range(100):
        if sock.exists():
            break
        time.sleep(0.05)

    def call(req):
        with socket.socket(socket.AF_UNIX) as s:
            s.connect(str(sock))
            s.sendall(json.dumps(req).encode() + b'\n')
            return json.loads(s.makefile().readline())

    st = call({'action': 'update_status'})
    assert st['ok'] and st['app']['current_version'] == '2.9.0-dev+dddd' and st['app']['state'] == 'idle', st
    publish(10, DIG3, '3.0.0-dev+eeee')
    assert call({'action': 'update_check'})['app']['available'] is True
    assert call({'action': 'update_apply', 'target': 'os'}) == {'ok': False, 'error': 'EpilykosOS updates are not published yet'}
    assert call({'action': 'update_apply', 'target': 'app'})['state'] == 'applying'
    for _ in range(100):
        if call({'action': 'update_status'})['app']['state'] == 'idle':
            break
        time.sleep(0.05)
    assert call({'action': 'update_status'})['app']['current_version'] == '3.0.0-dev+eeee'
finally:
    proc.terminate()
    out = proc.communicate(timeout=5)[0]
assert 'installing 3.0.0-dev+eeee' in out and 'promoted' in out, out
ok('hostd: update_status, update_check and update_apply over the socket')

print('\nPASS app update self-tests')
