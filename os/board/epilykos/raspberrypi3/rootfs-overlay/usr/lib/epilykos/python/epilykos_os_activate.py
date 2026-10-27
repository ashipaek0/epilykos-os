"""Finishing an OS update: Restart (explicit activation), Discard, the trial's
confirmation and reconciliation after every boot (OS update design §7.2–
§7.5, §9; plan task 7). Protocol-2 cards only.

The job's identity outside DATA is a bounded set of U-Boot environment
variables the slot loader understands (boot-slot.cmd):

  epk_os_pending          job ID           epk_os_target / epk_os_from   slots
  epk_os_target_sequence  signed sequence  epk_os_target_sha256  image SHA-256
  epk_os_phase            activation_intent -> activated -> confirming -> promoting
  epk_os_trial_started    set by the loader when it launches the target

Every step writes its intent before acting and is safe to repeat:

  Restart    revalidate (trust, space, app, the installed slot itself) ->
             env intent (activation_intent) -> rauc mark-active <target> ->
             read back -> activated -> reboot
  confirm    (healthy trial, called by epilykos-boot-health) confirming ->
             rauc mark-good -> floor and promoting in one batch -> app
             promotion on DATA -> journal confirmed -> env pending cleared
  rollback   source slot booted after the trial started: target marked bad,
             source primary again, journal rolled_back, env cleared. The
             floor is not raised.

A source boot before any trial (power cut around Restart) is not a
rollback: the job goes back to `ready` with the target non-bootable.
"""
import time

import epilykos_os_trust as trust
import epilykos_os_update as osu
import epilykos_storage as storage

PENDING = ('epk_os_pending', 'epk_os_target', 'epk_os_from', 'epk_os_target_sequence', 'epk_os_target_sha256',
           'epk_os_phase', 'epk_os_trial_started')
TRIAL_PHASES = ('activated', 'confirming', 'promoting')


def log(message):
    print(f'epilykos-os-update: {message}', flush=True)


def set_env(values):
    """One checked batch through the guarded fw_setenv (None deletes), read back."""
    script = ''.join(f'{name} {value}\n' if value is not None else f'{name}\n' for name, value in values.items())
    result = osu._run([trust.FW_SETENV, '--script', '-'], timeout=60, input=script)
    if result.returncode:
        raise osu.OsUpdateError('Could not save the OS update state')
    env = trust.read_env()
    for name, value in values.items():
        if env.get(name) != (None if value is None else str(value)):
            raise osu.OsUpdateError('The saved OS update state did not read back')
    return env


def clear_pending():
    return set_env({name: None for name in PENDING})


def target_bad(target):
    """Mark the target bad and show it cannot boot."""
    osu._run([osu.RAUC, 'status', 'mark-bad', osu.SLOTS[target]], timeout=120)
    env = trust.read_env()
    return env.get(f'BOOT_{target}_LEFT') == '0'


# -- hostd actions ------------------------------------------------------------------

def restart(job_id):
    """Administrator's Restart for a ready job; the worker activates and reboots."""
    with osu._lock:
        job = osu.journal()
        if not job or job['id'] != job_id or job['state'] != 'ready':
            raise osu.OsUpdateError('There is no OS update ready to finish')
        if osu.worker_active():
            raise osu.OsUpdateError('The OS update is busy; try again shortly')
        job['state'], job['error'] = 'restarting', None
        job['progress'] = {'phase': 'restarting'}
        osu.save(job)
        osu.start_worker()
    log(f'job {job_id}: restart to finish requested')
    return {'ok': True, 'os': osu.view(resume=False)}


def discard(job_id):
    """Drop a ready job: the target stays non-bootable, nothing is activated."""
    with osu._lock:
        job = osu.journal()
        if not job or job['id'] != job_id or job['state'] != 'ready':
            raise osu.OsUpdateError('There is no OS update ready to discard')
        if osu.worker_active() or trust.read_env().get('epk_os_pending'):
            raise osu.OsUpdateError('The OS update is being activated and can no longer be discarded')
        if not target_bad(job['target']):
            raise osu.OsUpdateError('Could not make sure the other system slot stays unused')
        osu.finish(job, 'discarded', None)
    return {'ok': True, 'os': osu.view(resume=False)}


# -- activation (worker) -------------------------------------------------------------

def activate(job):
    """Run by the worker for a `restarting` job, holding the maintenance lease."""
    if job.get('progress', {}).get('activated') and trust.read_env().get('epk_os_phase') == 'activated':
        osu._run([osu.SYSTEMCTL, '--no-block', 'reboot'], timeout=30)    # only the reboot was missing
        return 0
    try:
        revalidate(job)
        env = trust.read_env()
        if env.get('epk_os_pending') not in (None, '', job['id']):
            raise osu.OsUpdateError('Another OS update is recorded in the system state')
        set_env({'epk_os_pending': job['id'], 'epk_os_target': job['target'], 'epk_os_from': job['source'],
                 'epk_os_target_sequence': str(job['sequence']), 'epk_os_target_sha256': job['image']['sha256'],
                 'epk_os_phase': 'activation_intent', 'epk_os_trial_started': None})
    except (osu.OsUpdateError, trust.TrustError, storage.StorageError) as exc:
        return back_to_ready(job, str(exc))
    target = job['target']
    osu._run([osu.RAUC, 'status', 'mark-active', osu.SLOTS[target]], timeout=120)
    env = trust.read_env()
    if (env.get('BOOT_ORDER') or '').split()[:1] != [target] or env.get(f'BOOT_{target}_LEFT') != '3':
        return back_to_ready(job, 'The system could not be set to start the new version')
    set_env({'epk_os_phase': 'activated'})
    job['progress'] = {'phase': 'restarting', 'activated': True}
    osu.save(job)
    log(f'job {job["id"]}: slot {target} activated for one trial; restarting')
    osu._run([osu.SYSTEMCTL, '--no-block', 'reboot'], timeout=30)
    return 0


def revalidate(job):
    """Trust may have advanced, space shrunk or the app changed since ready."""
    plat = osu.platform()
    state = trust.current_state(plat)
    raw, sig = osu._unb64(job['entry']), osu._unb64(job['entry_sig'])
    entry = trust.verify_os_release(raw, sig, plat, state['policy'])
    trust.admissible(entry, plat, state['floor'])
    blockers = osu.app_blockers()
    if blockers:
        raise osu.OsUpdateError(blockers[0])
    storage.admit(osu.OCI_ALLOWANCE)
    osu.verify_target(job)


def back_to_ready(job, error):
    """Activation did not happen: target non-bootable, no pending state, ready."""
    try:
        safe = target_bad(job['target'])
        if safe:
            clear_pending()
    except (osu.OsUpdateError, trust.TrustError):
        safe = False
    if not safe:
        job['state'], job['error'] = 'reconciling', error + '; the system state needs checking'
        job['progress'] = {'phase': 'reconciling'}
        osu.save(job)
        osu.set_guard('reconciling', job['id'])
        return 1
    job['state'], job['error'] = 'ready', error
    job['progress'] = {'phase': 'ready'}
    osu.save(job)
    osu.set_guard('pending', job['id'])
    log(f'job {job["id"]}: not activated ({error})')
    return 1


# -- confirmation (epilykos-os-confirm, from boot health) -------------------------------

def confirm_candidate(job_id):
    """The trial is healthy. Idempotent from any phase."""
    env = trust.read_env()
    slot = osu.booted_slot()
    if env.get('epk_os_pending') != job_id or env.get('epk_os_target') != slot \
            or env.get('epk_os_trial_started') != job_id or env.get('epk_os_phase') not in TRIAL_PHASES:
        raise osu.OsUpdateError('This boot is not the trial of that OS update')
    sequence = int(env['epk_os_target_sequence'])
    if env['epk_os_phase'] == 'activated':
        env = set_env({'epk_os_phase': 'confirming'})
    if env['epk_os_phase'] == 'confirming':
        if osu._run([osu.RAUC, 'status', 'mark-good', 'booted'], timeout=120).returncode:
            raise osu.OsUpdateError('Could not confirm the new system')
        floor = max(int(env['epk_os_floor']), sequence)
        env = set_env({'epk_os_floor': str(floor), 'epk_os_phase': 'promoting'})
    if env.get(f'BOOT_{slot}_LEFT') != '3':
        raise osu.OsUpdateError('The confirmed system did not keep its attempts')
    promote_app()
    job = rebuild(env, osu.journal())
    osu.finish(job, 'confirmed', None)
    clear_pending()
    log(f'job {job_id}: sequence {sequence} confirmed in slot {slot}')
    return 0


def promote_app():
    """After OS confirmation only: a newer bundled app becomes the promoted
    one (same channel), so a later fallback keeps this confirmed app. A
    newer app already promoted from Settings stays."""
    import epilykos_update as update
    base = update.baseline()
    if not base['image_id'] or not base['sequence']:
        return
    current = update._read_json('current.json')
    same = bool(current) and current.get('channel') == base['channel']
    if same and int(current.get('sequence', 0)) >= base['sequence']:
        return
    promoted = {key: base[key] for key in ('release', 'channel', 'sequence', 'digest', 'image_id')}
    promoted.update(promoted_at=osu.now(), source='os-update')
    if same:
        update._write_json('previous.json', current)
    update._write_json('current.json', promoted)
    try:
        floor = int(open(update._path('floor')).read().strip())
    except (OSError, ValueError):
        floor = 0
    update._write('floor', f'{max(floor, base["sequence"])}\n')
    update._set_active(dict(promoted, baseline=False))


def rebuild(env, job):
    """The journal for the env's pending job; reconstructed after DATA was
    erased (factory reset during a trial) or lost."""
    pending = env.get('epk_os_pending')
    if job and job.get('id') == pending:
        return job
    job = {'id': pending, 'request_id': None, 'state': 'trying', 'sequence': int(env['epk_os_target_sequence']),
           'sha256': None, 'size': None, 'version': None, 'source': env.get('epk_os_from'),
           'target': env.get('epk_os_target'), 'image': {'sha256': env.get('epk_os_target_sha256')},
           'progress': {'phase': 'trying'}, 'error': None, 'created_at': osu.now(), 'reconstructed': True}
    osu.save(job)
    return job


# -- every boot (hostd start, before the app) -----------------------------------------

def reconcile_boot():
    """Make the journal and guard agree with the environment and the booted
    slot. Returns what was found, for the log."""
    try:
        env = trust.read_env()
        job = osu.journal()
        slot = osu.booted_slot()
    except (osu.OsUpdateError, trust.TrustError):
        osu.set_guard('reconciling')
        raise
    pending = env.get('epk_os_pending')
    if job and job['state'] in osu.WORKING + ('installing', 'restarting') and osu.worker_active():
        return job['state']                 # hostd restarted; the worker carries on
    if not pending:
        if job and job['state'] == 'restarting':
            back_to_ready(job, 'The restart was interrupted; restart again to finish')
            return osu.journal()['state']
        if job and job['state'] == 'trying':
            if slot == job['target'] and int(env.get('epk_os_floor', '0')) >= job['sequence']:
                osu.finish(job, 'confirmed', None)
                return 'confirmed'
            osu.finish(job, 'failed', 'The OS update ended without a result')
            return 'failed'
        osu.reconcile()
        return 'none'
    target, source = env.get('epk_os_target'), env.get('epk_os_from')
    if slot == target:
        job = rebuild(env, job)
        if job['state'] == 'confirmed':
            clear_pending()                 # confirmation finished but for this
            osu.set_guard('idle')
            return 'confirmed'
        job['state'], job['progress'] = 'trying', {'phase': 'trying'}
        osu.save(job)
        osu.set_guard('pending', job['id'])
        return 'trying'
    if slot != source:
        osu.set_guard('reconciling', pending)
        return 'reconciling'
    if env.get('epk_os_trial_started') == pending:
        # The trial ran and the loader fell back: a rollback. Floor unchanged.
        job = rebuild(env, job)
        if not target_bad(target):
            osu.set_guard('reconciling', pending)
            return 'reconciling'
        if (env.get('BOOT_ORDER') or '').split()[:1] != [slot]:
            osu._run([osu.RAUC, 'status', 'mark-active', 'booted'], timeout=120)
        if job['state'] != 'rolled_back':
            osu.finish(job, 'rolled_back', 'The new version did not start properly, so the previous one is running')
        clear_pending()
        return 'rolled_back'
    # Activation never reached a trial (power cut around Restart).
    if job and job.get('id') == pending and job['state'] in ('restarting', 'ready'):
        job['progress'] = {'phase': 'ready'}
        back_to_ready(job, 'The restart was interrupted; restart again to finish')
        return 'ready'
    job = rebuild(env, job)
    if target_bad(target):
        osu.finish(job, 'failed', 'The OS update was interrupted before it started')
        clear_pending()
        return 'failed'
    osu.set_guard('reconciling', pending)
    return 'reconciling'


def confirm_main(argv):
    if len(argv) != 2 or argv[0] != '--job' or not osu.JOB_ID.fullmatch(argv[1]):
        print('usage: epilykos-os-confirm --job <id>', flush=True)
        return 2
    for attempt in range(3):
        try:
            return confirm_candidate(argv[1])
        except (osu.OsUpdateError, trust.TrustError, OSError) as exc:
            log(f'confirmation: {exc}')
            time.sleep(5)
    return 1
