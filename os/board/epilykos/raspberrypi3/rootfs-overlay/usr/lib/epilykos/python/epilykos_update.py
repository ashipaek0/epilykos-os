"""Application updates for EpilykosOS (C-UPDATE-002, C-UPDATE-003, C-UPDATE-004,
D-UPDATE-001), driven from Epilykos Settings -> Updates through epilykos-hostd.

The OS image ships one Epilykos image, preloaded and pinned by ID
(image.env, the baseline). A newer one is installed only from a release
manifest signed with the application-release Ed25519 key, whose public half
is pinned in this image (/usr/lib/epilykos/app-manifest.pub). Merging the
manifest pull request in epilykos-os is the human trust decision; CI signs
the merged manifest and publishes it with the GitHub release app-<channel>
(.github/workflows/app-manifest-publish.yml).

  check()   fetch manifest.json + manifest.json.sig, verify the signature,
            the channel and the sequence floor; report whether it is newer
  apply()   pull <source>@<digest>, start it as the candidate, and promote it
            only once the container reports healthy; otherwise go back to
            the previous image. The boot slot is never touched.

State on DATA (/data/epilykos/updater, root only):

  current.json    promoted manifest + image ID (absent: the baseline runs)
  previous.json   the one before it (previous-known-good)
  floor           highest promoted sequence (anti-replay, never lowered)
  active.env      the image epilykos-run starts (candidate while one is tried)
  job.json        an update in progress
  result.json     how the last update ended

An OS update to a slot with a newer baseline wins over an older promoted
manifest: epilykos-run starts active.env only when its sequence is at least
the baseline's.
"""
import json
import os
import re
import subprocess
import tempfile
import threading
import time
import urllib.request

import epilykos_maintenance as maintenance

UPDATE_DIR = os.environ.get('EPILYKOS_UPDATE_DIR', '/data/epilykos/updater')
IMAGE_ENV = os.environ.get('EPILYKOS_IMAGE_ENV', '/usr/lib/epilykos/image.env')
PUBKEY = os.environ.get('EPILYKOS_APP_PUBKEY', '/usr/lib/epilykos/app-manifest.pub')
FEED = os.environ.get('EPILYKOS_UPDATE_URL', 'https://github.com/ashipaek0/epilykos-os/releases/download/app-{channel}')
PODMAN = os.environ.get('EPILYKOS_PODMAN', 'podman')
SYSTEMCTL = os.environ.get('EPILYKOS_SYSTEMCTL', 'systemctl')
OPENSSL = os.environ.get('EPILYKOS_OPENSSL', 'openssl')
REGISTRY = 'docker.io'
SERVICE = 'epilykos.service'
PULL_TIMEOUT_S = int(os.environ.get('EPILYKOS_PULL_TIMEOUT_S', 30 * 60))
# Type=notify with --sdnotify=healthy: `systemctl restart` returns once the
# container's healthcheck passes (start period 180 s) or the start fails.
START_TIMEOUT_S = int(os.environ.get('EPILYKOS_START_TIMEOUT_S', 10 * 60))
MAX_MANIFEST = 64 * 1024

DIGEST_RE = re.compile(r'^sha256:[0-9a-f]{64}$')
SOURCE_RE = re.compile(r'^[a-z0-9][a-z0-9._/-]{0,127}$')
IMAGE_ID_RE = re.compile(r'^[0-9a-f]{64}$')

_lock = threading.Lock()
_running = threading.Event()


class UpdateError(Exception):
    """A sentence Settings can show."""


def log(msg):
    print(f'epilykos-hostd: update: {msg}', flush=True)


# -- state files -------------------------------------------------------------------

def _path(name):
    return os.path.join(UPDATE_DIR, name)


def _read_json(name):
    try:
        with open(_path(name)) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _write(name, text):
    os.makedirs(UPDATE_DIR, mode=0o700, exist_ok=True)
    tmp = _path(name + '.tmp')
    with open(tmp, 'w') as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, _path(name))
    fd = os.open(UPDATE_DIR, os.O_RDONLY | os.O_DIRECTORY)
    try: os.fsync(fd)
    finally: os.close(fd)


def _write_json(name, value):
    _write(name, json.dumps(value, indent=2) + '\n')


def _remove(name):
    try:
        os.unlink(_path(name))
    except FileNotFoundError:
        pass


def baseline():
    """What this OS image preloaded (image.env)."""
    env = {}
    try:
        with open(IMAGE_ENV) as f:
            for line in f:
                if '=' in line and not line.startswith('#'):
                    k, v = line.strip().split('=', 1)
                    env[k] = v
    except OSError:
        pass
    ref = env.get('EPILYKOS_IMAGE_REF', '')
    try:
        seq = int(env.get('EPILYKOS_SEQUENCE', '0'))
    except ValueError:
        seq = 0
    return {
        'release': env.get('EPILYKOS_RELEASE', ''),
        'channel': env.get('EPILYKOS_CHANNEL', ''),
        'sequence': seq,
        'digest': ref.split('@', 1)[1] if '@' in ref else '',
        'image_id': env.get('EPILYKOS_IMAGE_ID', ''),
        'os_version': env.get('EPILYKOS_OS_VERSION', ''),
        'baseline': True,
    }


def current():
    """The promoted application release, or the baseline when it is newer."""
    base = baseline()
    cur = _read_json('current.json')
    if cur and int(cur.get('sequence', 0)) >= base['sequence']:
        return cur
    return base


def floor():
    try:
        with open(_path('floor')) as f:
            stored = int(f.read().strip())
    except (OSError, ValueError):
        stored = 0
    return max(stored, baseline()['sequence'])


def _set_active(release):
    """What epilykos-run starts next; the baseline needs no file."""
    if release.get('baseline'):
        _remove('active.env')
        return
    _write('active.env', 'EPILYKOS_ACTIVE_IMAGE_ID={}\nEPILYKOS_ACTIVE_SEQUENCE={}\nEPILYKOS_ACTIVE_RELEASE={}\n'.format(
        release['image_id'], int(release['sequence']), re.sub(r'[^A-Za-z0-9.+_-]', '', str(release['release']))))


# -- manifests ---------------------------------------------------------------------

def verify(manifest_bytes, signature):
    """Ed25519 signature check against the pinned key; returns the parsed manifest."""
    if not os.path.isfile(PUBKEY):
        raise UpdateError('This EpilykosOS build has no app update key, so it cannot install app updates. '
                          'Install a newer EpilykosOS image.')
    if len(signature) != 64:
        raise UpdateError('The update signature is not valid')
    with tempfile.TemporaryDirectory() as d:
        m, s = os.path.join(d, 'm'), os.path.join(d, 's')
        with open(m, 'wb') as f:
            f.write(manifest_bytes)
        with open(s, 'wb') as f:
            f.write(signature)
        r = subprocess.run([OPENSSL, 'pkeyutl', '-verify', '-pubin', '-inkey', PUBKEY, '-rawin', '-in', m, '-sigfile', s],
                           capture_output=True, timeout=30)
    if r.returncode != 0:
        raise UpdateError('The update is not signed by Epilykos, so it was refused')
    try:
        manifest = json.loads(manifest_bytes)
    except ValueError:
        raise UpdateError('The update manifest is unreadable')
    return validate(manifest)


def validate(m):
    """C-UPDATE-002 schema, on the signed content only."""
    app = (m.get('images') or {}).get('epilykos') if isinstance(m, dict) else None
    seq = m.get('sequence') if isinstance(m, dict) else None
    if (m.get('schema') != 1 or not isinstance(app, dict) or not isinstance(seq, int) or isinstance(seq, bool) or seq < 1
            or not DIGEST_RE.match(str(app.get('digest', ''))) or not SOURCE_RE.match(str(app.get('source', '')))
            or not isinstance(m.get('release'), str)):
        raise UpdateError('The update manifest is not valid')
    if m.get('channel') != baseline()['channel']:
        raise UpdateError(f'The update is for the {m.get("channel")} channel, not this appliance\'s')
    return {'release': m['release'], 'channel': m['channel'], 'sequence': seq,
            'digest': app['digest'], 'source': app['source']}


def _fetch(url):
    req = urllib.request.Request(url, headers={'User-Agent': 'epilykos-hostd'})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = r.read(MAX_MANIFEST + 1)
    if len(data) > MAX_MANIFEST:
        raise UpdateError('The update manifest is too large')
    return data


def fetch_latest():
    base = FEED.format(channel=baseline()['channel'] or 'stable').rstrip('/')
    try:
        manifest_bytes = _fetch(base + '/manifest.json')
        signature = _fetch(base + '/manifest.json.sig')
    except UpdateError:
        raise
    except Exception as e:  # noqa: BLE001 - network errors of every kind
        raise UpdateError(f'Could not reach the update server ({getattr(e, "reason", None) or e})')
    return verify(manifest_bytes, signature), manifest_bytes, signature


# -- actions -----------------------------------------------------------------------

def _app_view():
    cur = current()
    job = _read_json('job.json')
    return {
        'auto_install_supported': True,
        'current_digest': cur.get('digest', ''),
        'current_version': cur.get('release', ''),
        'state': 'applying' if job else 'idle',
        'job': job,
        'last_result': _read_json('result.json'),
    }


def os_view():
    # OS updates (signed RAUC bundles, C-UPDATE-001) are not published yet.
    return {'version': baseline()['os_version'], 'available': False}


def status():
    return {'ok': True, 'app': _app_view(), 'os': os_view()}


def check():
    app = _app_view()
    try:
        latest, manifest_bytes, signature = fetch_latest()
    except UpdateError as e:
        app.update(available=False, error=str(e))
        return {'ok': True, 'app': app, 'os': os_view()}
    newer = latest['sequence'] > floor() and latest['digest'] != app['current_digest']
    app.update(available=newer, latest_digest=latest['digest'], latest_version=latest['release'],
               latest_sequence=latest['sequence'])
    if newer:
        # apply() installs exactly what was verified here (and checks it again).
        _write_json('verified.json', {'manifest': manifest_bytes.decode(), 'signature': signature.hex()})
    return {'ok': True, 'app': app, 'os': os_view()}


def apply(target='app', automatic=False, sequence=None, digest=None):
    if target == 'os':
        raise UpdateError('EpilykosOS updates are not published yet')
    if target != 'app':
        raise UpdateError('unknown update target')
    with _lock:
        if _running.is_set() or _read_json('job.json'):
            raise UpdateError('An update is already running')
        saved = _read_json('verified.json')
        if saved:
            try:
                candidate = verify(saved['manifest'].encode(), bytes.fromhex(saved['signature']))
            except (UpdateError, KeyError, ValueError):
                candidate = None
        else:
            candidate = None
        if candidate is None:
            candidate = fetch_latest()[0]
        if automatic:
            if candidate['sequence'] != sequence or candidate['digest'] != digest:
                raise UpdateError('The verified release changed; check for updates again')
            attempted = _read_json('auto-attempt.json') or {}
            if attempted.get('sequence', 0) >= candidate['sequence']:
                return {'ok': True, 'state': 'already-attempted'}
        if candidate['sequence'] <= floor() or candidate['digest'] == current().get('digest'):
            return {'ok': True, 'state': 'current'}
        try:
            lease = maintenance.acquire()
        except maintenance.MaintenanceError as e:
            raise UpdateError(str(e)) from None
        try:
            job = {'state': 'downloading', 'started_at': _now(), 'release': candidate['release'],
                   'sequence': candidate['sequence'], 'digest': candidate['digest']}
            if automatic:
                _write_json('auto-attempt.json', {'sequence': candidate['sequence'], 'digest': candidate['digest']})
            _write_json('job.json', job)
            _running.set()
            threading.Thread(target=_run, args=(candidate, job, lease), daemon=True).start()
        except BaseException:
            _running.clear()
            try:
                _remove('job.json')
            finally:
                lease.close()
            raise
    log(f'installing {candidate["release"]} (sequence {candidate["sequence"]})')
    return {'ok': True, 'state': 'applying', 'message': 'Downloading the update'}


def _now():
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


def _restart_app():
    r = subprocess.run([SYSTEMCTL, 'restart', SERVICE], capture_output=True, text=True, timeout=START_TIMEOUT_S)
    return r.returncode == 0


def _run(candidate, job, lease):
    before = None
    started = False
    try:
        before = current()
        ref = f'{REGISTRY}/{candidate["source"]}@{candidate["digest"]}'
        r = subprocess.run([PODMAN, 'pull', '--quiet', ref], capture_output=True, text=True, timeout=PULL_TIMEOUT_S)
        if r.returncode != 0:
            raise UpdateError('Could not download the update: ' + (r.stderr.strip().splitlines() or ['podman pull failed'])[-1][:200])
        r = subprocess.run([PODMAN, 'image', 'inspect', '--format', '{{.Id}}', ref], capture_output=True, text=True, timeout=60)
        image_id = r.stdout.strip()
        if r.returncode != 0 or not IMAGE_ID_RE.match(image_id):
            raise UpdateError('The downloaded update could not be found in storage')
        cand = dict(candidate, image_id=image_id)
        _write_json('job.json', dict(job, state='starting'))
        _set_active(cand)
        started = True
        log(f'starting {cand["release"]}; waiting for it to report healthy')
        try:
            healthy = _restart_app()
        except subprocess.TimeoutExpired:
            healthy = False
        if not healthy:
            raise UpdateError('The new version did not start, so the previous version was put back')
        # Promote (C-UPDATE-002): current -> previous, floor raised.
        old_previous = _read_json('previous.json')
        if not before.get('baseline'):
            _write_json('previous.json', before)
        _write_json('current.json', dict(cand, promoted_at=_now()))
        _write('floor', f'{cand["sequence"]}\n')
        _write_json('result.json', {'ok': True, 'release': cand['release'], 'sequence': cand['sequence'], 'finished_at': _now()})
        log(f'{cand["release"]} is healthy and promoted')
        keep = {cand['image_id'], before.get('image_id'), baseline()['image_id']}
        if old_previous and old_previous.get('image_id') not in keep:
            subprocess.run([PODMAN, 'rmi', '--ignore', old_previous['image_id']], capture_output=True, timeout=120)
    except Exception as e:  # noqa: BLE001 - any failure ends in the previous image
        err = str(e) if isinstance(e, UpdateError) else f'The update failed ({type(e).__name__})'
        log(f'update failed: {err}')
        rolled_back = False
        try:
            if before is not None:
                _set_active(before)
            rolled_back = _restart_app() if started else before is not None  # nothing started: the old one never stopped
        except Exception as e2:  # noqa: BLE001
            log(f'rollback failed: {e2}')
        _write_json('result.json', {'ok': False, 'error': err, 'rolled_back': rolled_back,
                                    'release': candidate['release'], 'finished_at': _now()})
    finally:
        try:
            _remove('job.json')
            _remove('verified.json')
        finally:
            lease.close()
            _running.clear()


def recover():
    """At hostd start (before epilykos.service): an update cut short by a power
    loss or a hostd restart goes back to the last promoted image."""
    job = _read_json('job.json')
    if not job:
        return
    _set_active(current())
    _write_json('result.json', {'ok': False, 'error': 'The update was interrupted, so the previous version was kept',
                                'rolled_back': True, 'release': job.get('release', ''), 'finished_at': _now()})
    _remove('job.json')
    log('an interrupted update was rolled back')
