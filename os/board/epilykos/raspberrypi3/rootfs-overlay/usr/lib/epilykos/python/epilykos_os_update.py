"""OS updates from Settings, protocol-2 cards (OS update design §7–§8; plan
tasks 5–6): check, download, verify and install a signed package into the
other slot, leaving it ready but not bootable until the administrator
chooses Restart (activation is epilykos_os_activate, plan task 7).

hostd only admits a job and reads its state. The work is done by
epilykos-os-update.service (this module's run_worker), so restarting hostd
neither stops nor duplicates an install:

  queued -> downloading -> verifying -> installing -> ready
                 \\-> cancelled (before installing)    \\-> failed

State on DATA, /data/epilykos/os-updater:

  private/ (0700)   journal.json   the job: request ID, exact signed entry
                                   and policy, slots, RAUC identity, progress
                    staging/       the package, owned and retained until the
                                   job ends
                    cancel         a cancellation request for the worker
  public/ (0755)    guard.json     {"schema": 1, "state": idle|active|pending|
                                   reconciling, "job": ...}, read-only inside
                                   the app (/run/epilykos-os-updater)

Every step re-verifies trust from the shared environment state
(epilykos_os_trust). RAUC is given the job ID as its transaction ID; the
installed slot is then checked independently (its first image-size bytes
hash to the signed image SHA-256) and must still be non-bootable.
"""
import base64
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
import uuid

import epilykos_app_compat as compat
import epilykos_maintenance as maintenance
import epilykos_os_trust as trust
import epilykos_storage as storage

BASE = Path(os.environ.get('EPILYKOS_OS_UPDATE_DIR', '/data/epilykos/os-updater'))
PRIVATE = BASE / 'private'
PUBLIC = BASE / 'public'
STAGING = PRIVATE / 'staging'
FEED = os.environ.get('EPILYKOS_OS_FEED', 'https://github.com/ashipaek0/epilykos-os/releases/download/os-{channel}')
HEALTH = os.environ.get('EPILYKOS_OS_HEALTH', '/run/epilykos/os-health.json')
CMDLINE = os.environ.get('EPILYKOS_CMDLINE', '/proc/cmdline')
RAUC = os.environ.get('EPILYKOS_RAUC', 'rauc')
SYSTEMCTL = os.environ.get('EPILYKOS_SYSTEMCTL', 'systemctl')
BUSCTL = os.environ.get('EPILYKOS_BUSCTL', 'busctl')
PODMAN = os.environ.get('EPILYKOS_PODMAN', 'podman')
WORKER = 'epilykos-os-update.service'
SLOTS = {'A': 'rootfs.0', 'B': 'rootfs.1'}
DEVICES = {'A': os.environ.get('EPILYKOS_SLOT_A', '/dev/disk/by-partuuid/45504c4b-02'),
           'B': os.environ.get('EPILYKOS_SLOT_B', '/dev/disk/by-partuuid/45504c4b-03')}
CHUNK = 1024 * 1024
METADATA_MAX = 64 * 1024
# Until the new slot's app image import is measured on hardware (design
# §8.3), admission reserves a full slot's worth for it on DATA.
OCI_ALLOWANCE = trust.SLOT_BYTES
DOWNLOAD_ATTEMPTS = int(os.environ.get('EPILYKOS_OS_DOWNLOAD_ATTEMPTS', '5'))
RETRY_S = float(os.environ.get('EPILYKOS_OS_RETRY_S', '5'))
INSTALL_WAIT_S = 4 * 3600
GUARD_STATES = ('idle', 'active', 'pending', 'reconciling')
WORKING = ('queued', 'downloading', 'verifying')
TERMINAL = ('confirmed', 'rolled_back', 'failed', 'cancelled', 'discarded')
REQUEST_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9-]{7,63}')
JOB_ID = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}')

_lock = threading.Lock()


class OsUpdateError(Exception):
    """A sentence Settings can show."""


class Cancelled(Exception):
    pass


def log(message):
    print(f'epilykos-os-update: {message}', flush=True)


def now():
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


# -- durable files ---------------------------------------------------------------

def _sync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_atomic(path, data, mode):
    path = Path(path)
    temporary = path.with_name(f'.{path.name}.{os.getpid()}.tmp')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, mode)
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(temporary, path)
    _sync_dir(path.parent)


def _layout():
    for path, mode in ((BASE, 0o755), (PRIVATE, 0o700), (STAGING, 0o700), (PUBLIC, 0o755)):
        path.mkdir(mode=mode, parents=True, exist_ok=True)
        if path.is_symlink() or not path.is_dir():
            raise OsUpdateError('The OS update state directory is not usable')
        os.chmod(path, mode)


def journal():
    try:
        with open(PRIVATE / 'journal.json', 'rb') as stream:
            value = json.loads(stream.read(1024 * 1024))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        raise OsUpdateError('The OS update record is unreadable; OS updates are off until it is repaired') from exc
    if not isinstance(value, dict) or value.get('schema') != 1 or not isinstance(value.get('job'), dict):
        raise OsUpdateError('The OS update record is unreadable; OS updates are off until it is repaired')
    return value['job']


def save(job):
    _layout()
    job['updated_at'] = now()
    _write_atomic(PRIVATE / 'journal.json', json.dumps({'schema': 1, 'job': job}, sort_keys=True).encode(), 0o600)


def guard():
    """The public guard; missing or malformed reads as `reconciling` (fail closed)."""
    try:
        with open(PUBLIC / 'guard.json', 'rb') as stream:
            value = json.loads(stream.read(4096))
        if value.get('schema') == 1 and value.get('state') in GUARD_STATES:
            return value
    except (OSError, ValueError, AttributeError):
        pass
    return {'schema': 1, 'state': 'reconciling', 'job': None}


def set_guard(state, job_id=None):
    if state not in GUARD_STATES:
        raise ValueError(state)
    _layout()
    _write_atomic(PUBLIC / 'guard.json', json.dumps({'schema': 1, 'state': state, 'job': job_id},
                                                     sort_keys=True).encode() + b'\n', 0o644)


def blocking():
    """What other maintenance must respect: the guard, or `active` for a
    queued job the worker has not taken up yet. Protocol-1 cards have no OS
    update jobs, so nothing is blocked there."""
    try:
        if platform() is None:
            return 'idle'
    except OsUpdateError:
        return 'reconciling'
    state = guard()['state']
    if state != 'idle':
        return state
    try:
        job = journal()
    except OsUpdateError:
        return 'reconciling'
    return 'active' if job and job['state'] in WORKING + ('installing',) else 'idle'


def refuse_if(*states, action):
    state = blocking()
    if state in states:
        message = {'active': 'an OS update is being installed', 'pending': 'an OS update is ready to finish',
                   'reconciling': 'an OS update needs checking'}[state]
        raise OsUpdateError(f'{action} is not possible while {message}. Use Settings > Updates.')


# -- this box ----------------------------------------------------------------------

def platform():
    """This slot's platform, or None on a protocol-1 card."""
    try:
        return trust.load_platform()
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        raise OsUpdateError('This system\'s platform record is invalid; OS updates are off') from exc


def booted_slot():
    with open(CMDLINE) as stream:
        for word in stream.read().split():
            if word.startswith('rauc.slot=') and word[10:] in SLOTS:
                return word[10:]
    raise OsUpdateError('Could not tell which system slot is running')


def other(slot):
    return 'B' if slot == 'A' else 'A'


def _run(argv, timeout=60, **kwargs):
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=timeout, **kwargs)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise OsUpdateError(f'{Path(argv[0]).name} did not answer') from exc


def health_blockers(slot, env):
    blockers = []
    try:
        with open(HEALTH) as stream:
            health = json.load(stream)
    except (OSError, ValueError):
        health = {}
    if health.get('state') != 'confirmed' or health.get('slot') != slot:
        blockers.append('The running system has not been confirmed healthy since it started')
    if env.get('epk_os_pending'):
        blockers.append('An earlier OS update has not finished')
    if (env.get('BOOT_ORDER') or '').split()[:1] != [slot]:
        blockers.append('The running system is not the one the box starts by default')
    return blockers


def app_blockers():
    try:
        result = _run([PODMAN, 'container', 'inspect', 'epilykos'], timeout=30)
        if result.returncode:
            raise compat.CompatibilityError('The app is not running')
        compat.os_update_guard(compat.inspection(result.stdout))
    except (compat.CompatibilityError, OsUpdateError) as exc:
        return [str(exc)]
    return []


def required_bytes(entry):
    return entry['bundle']['size'] + OCI_ALLOWANCE


# -- release feed -----------------------------------------------------------------

def _open(url, headers=None):
    """One HTTP(S) GET; replaced by tests."""
    request = urllib.request.Request(url, headers={'User-Agent': 'epilykos-hostd', **(headers or {})})
    return urllib.request.urlopen(request, timeout=60)


def _fetch(url, maximum):
    try:
        with _open(url) as response:
            data = response.read(maximum + 1)
    except (OSError, http.client.HTTPException, ValueError) as exc:
        raise OsUpdateError(f'Could not reach the OS update server ({getattr(exc, "reason", None) or exc})') from exc
    if len(data) > maximum:
        raise OsUpdateError('The OS update information is too large')
    return data


def fetch_release(plat):
    base = FEED.format(channel=plat['channel']).rstrip('/')
    name = f'os-index-{plat["channel"]}.json'
    return {'entry': _fetch(f'{base}/{name}', trust.ENTRY_MAX), 'entry_sig': _fetch(f'{base}/{name}.sig', 64),
            'policy': _fetch(f'{base}/os-policy.json', trust.POLICY_MAX),
            'policy_sig': _fetch(f'{base}/os-policy.sig', 64)}


def verify_release(files, plat, state):
    """(entry, policy, policy_is_newer) under the current shared state; a
    newer root-signed policy is verified here, persisted only by the worker."""
    try:
        policy = trust.verify_policy(files['policy'], files['policy_sig'], Path(trust.ROOT_PEM).read_bytes(),
                                     board=plat['board'], channel=plat['channel'],
                                     current={'raw': state['raw'], 'trust_epoch': state['trust_epoch']})
    except (OSError, ValueError) as exc:
        raise OsUpdateError('The published trust policy does not verify') from exc
    entry = trust.verify_os_release(files['entry'], files['entry_sig'], plat, policy)
    return entry, policy, policy['trust_epoch'] > state['trust_epoch']


def _b64(data):
    return base64.b64encode(data).decode()


def _unb64(text):
    return base64.b64decode(text, validate=True)


# -- hostd actions ----------------------------------------------------------------

def public(job):
    if not job:
        return None
    keys = ('id', 'request_id', 'state', 'sequence', 'version', 'size', 'notes_url', 'progress', 'error',
            'source', 'target', 'created_at', 'updated_at')
    return {key: job.get(key) for key in keys}


def view(resume=True):
    """What Settings shows; never fails. Looking resumes a job whose worker
    went away (power cut, crash)."""
    try:
        plat = platform()
    except OsUpdateError as exc:
        return {'supported': False, 'reason': str(exc)}
    if plat is None:
        return {'supported': False,
                'reason': 'This card was made before OS updates from Settings. Flash a current EpilykosOS image once '
                          'to get them.'}
    result = {'supported': True, 'version': plat['os_version'], 'sequence': plat['os_sequence'],
              'channel': plat['channel'], 'guard': guard()['state']}
    try:
        job = journal()
        if resume:
            ensure_worker(job)
        result['job'] = public(job)
    except OsUpdateError as exc:
        result.update(job=None, error=str(exc))
    return result


def status():
    return {'ok': True, 'os': view()}


def check():
    plat = platform()
    if plat is None:
        return status()
    state = trust.current_state(plat)
    files = fetch_release(plat)
    entry, policy, newer = verify_release(files, plat, state)
    result = view()
    try:
        trust.admissible(entry, plat, state['floor'])
    except trust.TrustError:
        result.update(available=False, latest_version=entry['version'], latest_sequence=entry['sequence'])
        return {'ok': True, 'os': result}
    env = trust.read_env()
    slot = booted_slot()
    blockers = health_blockers(slot, env) + app_blockers()
    try:
        space = storage.admit(required_bytes(entry))
    except storage.StorageError as exc:
        space, blockers = None, blockers + [str(exc)]
    _layout()
    _write_atomic(PRIVATE / 'checked.json', json.dumps({k: _b64(v) for k, v in files.items()}).encode(), 0o600)
    result.update(available=True, blockers=blockers, space=space, trust_update=newer,
                  candidate={'sequence': entry['sequence'], 'version': entry['version'],
                             'size': entry['bundle']['size'], 'sha256': entry['bundle']['sha256'],
                             'notes_url': entry['notes_url']})
    return {'ok': True, 'os': result}


def install(sequence, sha256, request_id):
    """Admit one job for the exact checked package; idempotent by request ID."""
    if type(sequence) is not int or not isinstance(sha256, str) or not re.fullmatch('[0-9a-f]{64}', sha256) \
            or not isinstance(request_id, str) or not REQUEST_ID.fullmatch(request_id):
        raise OsUpdateError('Check for OS updates again, then install')
    plat = platform()
    if plat is None:
        raise OsUpdateError(view()['reason'])
    with _lock:
        job = journal()
        if job and job.get('request_id') == request_id:
            if (job['sequence'], job['sha256']) != (sequence, sha256):
                raise OsUpdateError('That request was for a different OS update')
            return {'ok': True, 'os': view()}
        if job and job['state'] not in TERMINAL:
            raise OsUpdateError('An OS update is already in progress')
        if guard()['state'] != 'idle':
            raise OsUpdateError('An OS update needs checking before another can start')
        try:
            with open(PRIVATE / 'checked.json', 'rb') as stream:
                files = {k: _unb64(v) for k, v in json.loads(stream.read(METADATA_MAX * 4)).items()}
        except (OSError, ValueError, AttributeError) as exc:
            raise OsUpdateError('Check for OS updates again, then install') from exc
        state = trust.current_state(plat)
        entry, _, _ = verify_release(files, plat, state)
        if (entry['sequence'], entry['bundle']['sha256']) != (sequence, sha256):
            raise OsUpdateError('The verified OS update changed; check again')
        trust.admissible(entry, plat, state['floor'])
        slot = booted_slot()
        blockers = health_blockers(slot, trust.read_env()) + app_blockers()
        if blockers:
            raise OsUpdateError(blockers[0])
        storage.admit(required_bytes(entry))
        job = {'id': str(uuid.uuid4()), 'request_id': request_id, 'state': 'queued', 'sequence': sequence,
               'sha256': sha256, 'size': entry['bundle']['size'], 'version': entry['version'],
               'url': entry['bundle']['url'], 'notes_url': entry['notes_url'], 'source': slot, 'target': other(slot),
               'entry': _b64(files['entry']), 'entry_sig': _b64(files['entry_sig']),
               'policy': _b64(files['policy']), 'policy_sig': _b64(files['policy_sig']),
               'progress': {'phase': 'queued'}, 'error': None, 'created_at': now()}
        _remove(PRIVATE / 'cancel')
        save(job)
        start_worker()
    log(f'OS update {job["version"]} (sequence {sequence}) admitted as job {job["id"]}')
    return {'ok': True, 'os': view(resume=False)}


def cancel(job_id):
    with _lock:
        job = journal()
        if not job or job['id'] != job_id:
            raise OsUpdateError('That OS update is not running')
        if job['state'] not in WORKING:
            raise OsUpdateError('The OS update can no longer be cancelled; it will finish installing first')
        _write_atomic(PRIVATE / 'cancel', job_id.encode(), 0o600)
        if not worker_active():
            finish(job, 'cancelled', None)
    return {'ok': True, 'os': view(resume=False)}


def dismiss(job_id):
    with _lock:
        job = journal()
        if not job or job['id'] != job_id or job['state'] not in TERMINAL:
            raise OsUpdateError('There is no finished OS update to dismiss')
        _remove(PRIVATE / 'journal.json')
    return {'ok': True, 'os': view()}


def _remove(path):
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass


def worker_active():
    return _run([SYSTEMCTL, 'is-active', '--quiet', WORKER], timeout=15).returncode == 0


def start_worker():
    result = _run([SYSTEMCTL, 'start', '--no-block', WORKER], timeout=30)
    if result.returncode:
        raise OsUpdateError('Could not start the OS update')


def ensure_worker(job):
    """A job whose worker went away (power, crash) resumes on the next look."""
    if job and job['state'] in WORKING + ('installing',) and not worker_active():
        start_worker()


def reconcile():
    """At hostd start, before the app: the guard always reflects the journal,
    and an interrupted job resumes (download) or is reconciled (install)."""
    _layout()
    try:
        job = journal()
    except OsUpdateError:
        set_guard('reconciling')
        raise
    if not job or job['state'] in TERMINAL:
        set_guard('idle')
        return
    if job['state'] == 'ready':
        set_guard('pending', job['id'])
    elif job['state'] == 'reconciling':
        set_guard('reconciling', job['id'])
    else:
        set_guard('active', job['id'])
        if not worker_active():
            start_worker()


# -- the worker -----------------------------------------------------------------

def step(job, phase, **progress):
    job['state'] = phase
    job['progress'] = {'phase': phase, **progress}
    save(job)


def finish(job, state, error):
    job['state'], job['error'] = state, error
    job['progress'] = {'phase': state}
    job['finished_at'] = now()
    save(job)
    if state in TERMINAL:
        for path in STAGING.glob(f'{job["id"]}.*'):
            _remove(path)
        # The cancel request (naming this job only) stays: a worker that was
        # just starting still sees it. The next admission removes it.
        set_guard('idle')
    log(f'job {job["id"]}: {state}' + (f' ({error})' if error else ''))


def cancelled(job):
    try:
        return (PRIVATE / 'cancel').read_text() == job['id']
    except OSError:
        return False


def run_worker():
    job = journal()
    if not job or job['state'] not in WORKING + ('installing',):
        return 0
    if job['state'] in WORKING and cancelled(job):
        finish(job, 'cancelled', None)
        return 0
    while True:
        try:
            lease = maintenance.acquire()
            break
        except maintenance.MaintenanceError as exc:
            if job['state'] == 'installing':
                time.sleep(5)       # RAUC may still be writing: never give up the guard
                continue
            finish(job, 'failed', str(exc))
            return 1
    try:
        set_guard('active', job['id'])
        if job['state'] == 'installing':
            return reconcile_install(job)
        return work(job)
    finally:
        lease.close()


def work(job):
    plat = platform()
    try:
        state = trust.current_state(plat)
        policy_raw, policy_sig = _unb64(job['policy']), _unb64(job['policy_sig'])
        files = {'entry': _unb64(job['entry']), 'entry_sig': _unb64(job['entry_sig']),
                 'policy': policy_raw, 'policy_sig': policy_sig}
        entry, policy, newer = verify_release(files, plat, state)
        if newer:
            # Trust advances first and stays advanced whatever this job does.
            state = trust.admit_policy(policy_raw, policy_sig, plat, state)
            restart_rauc()
            log(f'trust policy epoch {state["trust_epoch"]} admitted')
        entry = trust.verify_os_release(files['entry'], files['entry_sig'], plat, state['policy'])
        trust.admissible(entry, plat, state['floor'])
        if (entry['sequence'], entry['bundle']['sha256']) != (job['sequence'], job['sha256']):
            raise OsUpdateError('The OS update record does not match its authorization')
        bundle = download(job, entry)
        step(job, 'verifying')
        image = verify_bundle(bundle, entry, state['policy'])
        job['image'] = image
        if cancelled(job):
            raise Cancelled()
        install_bundle(job, bundle)
        return 0
    except Cancelled:
        finish(job, 'cancelled', None)
        return 0
    except (OsUpdateError, trust.TrustError, storage.StorageError, ValueError, OSError) as exc:
        message = str(exc) if isinstance(exc, (OsUpdateError, trust.TrustError, storage.StorageError)) \
            else f'The OS update failed ({type(exc).__name__})'
        if job['state'] == 'installing':
            return fail_install(job, message)
        finish(job, 'failed', message)
        return 1


def restart_rauc():
    """The keyring is regenerated by rauc.service's ExecStartPre."""
    if rauc_operation()[0] not in ('idle', None):
        raise OsUpdateError('The system updater is busy; try again shortly')
    if _run([SYSTEMCTL, 'restart', 'rauc.service'], timeout=120).returncode:
        raise OsUpdateError('Could not restart the system updater with the new trust policy')


# -- download --------------------------------------------------------------------

def download(job, entry):
    """Stream to owned staging in 1 MiB writes, resuming only the same signed
    package with a validator and a matching 206 Content-Range."""
    _layout()
    final = STAGING / f'{job["id"]}.raucb'
    part = STAGING / f'{job["id"]}.part'
    size = entry['bundle']['size']
    if final.exists() and final.stat().st_size == size:
        return final
    step(job, 'downloading', bytes=_size(part), total=size)
    for attempt in range(1, DOWNLOAD_ATTEMPTS + 1):
        try:
            _transfer(job, entry, part)
            break
        except (urllib.error.URLError, http.client.HTTPException, socket.timeout, ConnectionError) as exc:
            if attempt == DOWNLOAD_ATTEMPTS:
                raise OsUpdateError('The download kept failing; check the network and try again') from exc
            log(f'download interrupted ({type(exc).__name__}); resuming')
            time.sleep(RETRY_S * attempt)
    os.replace(part, final)
    _sync_dir(STAGING)
    return final


def _size(path):
    try:
        return path.stat().st_size
    except FileNotFoundError:
        return 0


def _transfer(job, entry, part):
    size = entry['bundle']['size']
    offset = _size(part)
    if offset > size:
        offset = 0
    headers = {}
    if offset and job.get('validator'):
        headers = {'Range': f'bytes={offset}-', 'If-Range': job['validator']}
    else:
        offset = 0
    with _open(entry['bundle']['url'], headers) as response:
        code = getattr(response, 'status', None) or response.getcode()
        if offset and code == 206:
            if response.headers.get('Content-Range') != f'bytes {offset}-{size - 1}/{size}':
                os.truncate(part, 0)    # not the part we asked for: start again
                raise ConnectionError('unexpected Content-Range')
        elif code == 200:
            offset = 0              # Range ignored or the file changed: start again
            validator = response.headers.get('ETag') or response.headers.get('Last-Modified')
            job['validator'] = validator if validator and not validator.startswith('W/') else None
        else:
            raise OsUpdateError(f'The OS update server answered {code}')
        fd = os.open(part, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            os.ftruncate(fd, offset)
            os.lseek(fd, offset, os.SEEK_SET)
            written, last = offset, 0.0
            while True:
                if cancelled(job):
                    raise Cancelled()
                chunk = response.read(CHUNK)
                if not chunk:
                    break
                if written + len(chunk) > size:
                    raise OsUpdateError('The download is larger than the signed size')
                os.write(fd, chunk)
                written += len(chunk)
                if time.monotonic() - last >= 2:
                    os.fsync(fd)
                    storage.check_headroom()
                    step(job, 'downloading', bytes=written, total=size)
                    last = time.monotonic()
            os.fsync(fd)
        finally:
            os.close(fd)
    if written != size:
        raise ConnectionError('download ended early')
    step(job, 'downloading', bytes=written, total=size)


def verify_bundle(bundle, entry, policy):
    """Exact bytes, actual signer and signed manifest, before RAUC sees it.
    Returns the signed rootfs image identity for the installed-slot check."""
    keyring = PRIVATE / 'keyring.pem'
    trust.write_keyring(policy, keyring)
    try:
        trust.check_bundle(bundle, entry, policy, keyring)
        manifest, _ = trust.bundle_signer(bundle, keyring)
    finally:
        _remove(keyring)
    image = trust.check_manifest(manifest, entry)['image.rootfs']
    size, sha256 = int(image.get('size', '0')), image.get('sha256', '')
    if not 0 < size <= trust.SLOT_BYTES or not re.fullmatch('[0-9a-f]{64}', sha256):
        raise OsUpdateError('The OS update image does not fit a system slot')
    return {'size': size, 'sha256': sha256}


# -- RAUC ------------------------------------------------------------------------

def rauc_operation():
    """(Operation, unique bus owner) of the RAUC service, (None, None) if absent."""
    owner = _run([BUSCTL, 'call', 'org.freedesktop.DBus', '/org/freedesktop/DBus', 'org.freedesktop.DBus',
                  'GetNameOwner', 's', 'de.pengutronix.rauc'], timeout=15)
    if owner.returncode:
        return None, None
    operation = _run([BUSCTL, 'get-property', 'de.pengutronix.rauc', '/', 'de.pengutronix.rauc.Installer',
                      'Operation'], timeout=15)
    return (_busctl_string(operation.stdout) if operation.returncode == 0 else None), _busctl_string(owner.stdout)


def _busctl_string(text):
    match = re.fullmatch(r'\s*s\s+"([^"]*)"\s*', text or '')
    return match.group(1) if match else None


def rauc_identity():
    if _run([SYSTEMCTL, 'start', 'rauc.service'], timeout=120).returncode:
        raise OsUpdateError('The system updater did not start')
    operation, owner = rauc_operation()
    invocation = _run([SYSTEMCTL, 'show', '--property=InvocationID', '--value', 'rauc.service'], timeout=15)
    if operation != 'idle' or not owner or invocation.returncode or not invocation.stdout.strip():
        raise OsUpdateError('The system updater is busy or unavailable')
    return {'transaction': None, 'bus_owner': owner, 'invocation': invocation.stdout.strip()}


def install_bundle(job, bundle):
    identity = rauc_identity()
    identity['transaction'] = job['id']
    job['rauc'] = identity
    step(job, 'installing', percent=0)          # durable before RAUC is called
    process = subprocess.Popen([RAUC, 'install', f'--transaction-id={job["id"]}', str(bundle)],
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, text=True)
    last_line, last_save = '', 0.0
    for line in process.stdout:
        line = line.strip()
        if line:
            last_line = line
        match = re.match(r'^(\d{1,3})%\s+(.*)$', line)
        if match and time.monotonic() - last_save >= 2:
            step(job, 'installing', percent=min(int(match.group(1)), 100), message=match.group(2)[:120])
            last_save = time.monotonic()
    code = process.wait()
    if code:
        return fail_install(job, 'The system updater refused or failed the OS update' +
                            (f': {last_line[-160:]}' if last_line else ''))
    return complete_install(job)


def complete_install(job):
    try:
        verify_target(job)
    except OsUpdateError as exc:
        return fail_install(job, str(exc))
    job['state'] = 'ready'
    job['progress'] = {'phase': 'ready'}
    save(job)
    set_guard('pending', job['id'])         # before the maintenance lease is released
    log(f'job {job["id"]}: ready in slot {job["target"]}; restart to finish')
    return 0


def verify_target(job):
    """The installed slot holds exactly the signed image and cannot boot yet."""
    env = trust.read_env()
    target = job['target']
    if env.get(f'BOOT_{target}_LEFT') != '0' or (env.get('BOOT_ORDER') or '').split()[:1] != [job['source']]:
        raise OsUpdateError('The installed system is unexpectedly bootable')
    image = job['image']
    digest, remaining = hashlib.sha256(), image['size']
    try:
        with open(DEVICES[target], 'rb') as device:
            while remaining:
                chunk = device.read(min(CHUNK, remaining))
                if not chunk:
                    break
                digest.update(chunk)
                remaining -= len(chunk)
    except OSError as exc:
        raise OsUpdateError('Could not read back the installed system') from exc
    if remaining or digest.hexdigest() != image['sha256']:
        raise OsUpdateError('The installed system does not match the signed image')


def fail_install(job, message):
    """RAUC marked the target bad before writing; make sure it stays so, and
    say `reconciling` rather than guess when that cannot be shown."""
    target = job['target']
    _run([RAUC, 'status', 'mark-bad', SLOTS[target]], timeout=120)
    try:
        env = trust.read_env()
        safe = env.get(f'BOOT_{target}_LEFT') == '0' and (env.get('BOOT_ORDER') or '').split()[:1] == [job['source']]
    except trust.TrustError:
        safe = False
    if not safe:
        job['state'], job['error'] = 'reconciling', message + '; the other system slot needs checking'
        job['progress'] = {'phase': 'reconciling'}
        save(job)
        set_guard('reconciling', job['id'])
        log(f'job {job["id"]}: could not show slot {target} is non-bootable')
        return 1
    finish(job, 'failed', message)
    return 1


def reconcile_install(job):
    """The worker died while RAUC was installing. Only the exact RAUC writer
    that was recorded can complete it; anything else is interrupted."""
    recorded = job.get('rauc') or {}
    deadline = time.monotonic() + INSTALL_WAIT_S
    while True:
        operation, owner = rauc_operation()
        invocation = _run([SYSTEMCTL, 'show', '--property=InvocationID', '--value', 'rauc.service'], timeout=15)
        same = owner is not None and owner == recorded.get('bus_owner') and \
            invocation.stdout.strip() == recorded.get('invocation')
        if same and operation == 'installing' and time.monotonic() < deadline:
            time.sleep(5)
            continue
        break
    if same and operation == 'idle':
        last = _run([BUSCTL, 'get-property', 'de.pengutronix.rauc', '/', 'de.pengutronix.rauc.Installer',
                     'LastError'], timeout=15)
        if last.returncode == 0 and _busctl_string(last.stdout) == '':
            log(f'job {job["id"]}: RAUC finished while the worker was away; checking the slot')
            return complete_install(job)
        return fail_install(job, 'The system updater failed the OS update')
    if same:
        return fail_install(job, 'The system updater did not finish in time')
    # A different or no RAUC service: the old one (and its cgroup) is gone.
    return fail_install(job, 'The OS update was interrupted while installing')


def main():
    try:
        return run_worker()
    except (OsUpdateError, trust.TrustError) as exc:
        log(str(exc))
        return 1
