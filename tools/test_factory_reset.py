#!/usr/bin/env python3
"""Self-tests for the factory reset (C-HOST-001): asking for it
(epilykos_reset, epilykos-factory-reset on the console) and carrying it out
(epilykos-factory-reset-boot, run here on an ext4 image file standing in
for DATA). hostd's factory_reset action is tested in test_host_actions.py.
"""
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True  # keep __pycache__ out of the rootfs overlay
ROOT = Path(__file__).resolve().parent.parent
OVERLAY = ROOT / 'os/board/epilykos/raspberrypi3/rootfs-overlay'
PYLIB = OVERLAY / 'usr/lib/epilykos/python'
BOOT_STEP = OVERLAY / 'usr/bin/epilykos-factory-reset-boot'
sys.path.insert(0, str(PYLIB))


def ok(name):
    print(f'ok - {name}')


with tempfile.TemporaryDirectory() as d:
    d = Path(d)
    boot, data = d / 'boot', d / 'data'
    boot.mkdir()
    data.mkdir()
    os.environ['EPILYKOS_BOOT_DIR'], os.environ['EPILYKOS_DATA_DIR'] = str(boot), str(data)
    import epilykos_reset as reset  # noqa: E402  (reads the directories above)

    # -- what counts as asking ------------------------------------------------------
    for text in ('ERASE', 'erase\n', '  Erase  \r\n', '﻿ERASE\r\nanything else', 'note\nERASE\n', b'ERASE\r\n'):
        assert reset.confirmed(text), repr(text)
    for text in ('', 'yes', 'please erase', 'ERASE ALL', 'E R A S E', b'\xff\xfe'):
        assert not reset.confirmed(text), repr(text)
    for confirm in (None, '', 'yes', 'ERASE ALL', 1):
        try:
            reset.check(confirm)
        except reset.ResetError as e:
            assert 'ERASE' in str(e)
        else:
            raise AssertionError(f'{confirm!r} accepted')
    reset.check(' erase ')
    ok('only ERASE on a line of its own asks for a reset (any case, CRLF and a BOM allowed)')

    path = Path(reset.request('Epilykos settings'))
    assert path == boot / 'factory-reset.txt' and reset.pending()
    raw = path.read_bytes()
    assert raw.startswith(b'ERASE\r\n') and b'Epilykos settings' in raw and b'delete this file' in raw
    assert not list(boot.glob('*.tmp'))
    ok('request: factory-reset.txt on BOOT, ERASE first, readable in Notepad, says how to cancel')

    (data / reset.DONE_FILE).write_text('x\n')
    assert not reset.forget_done() and (data / reset.DONE_FILE).exists(), 'kept while the request is on BOOT'
    path.unlink()
    assert not reset.pending()
    assert reset.forget_done() and not (data / reset.DONE_FILE).exists()
    assert not reset.forget_done()
    src = (OVERLAY / 'usr/bin/epilykos-wifi-import').read_text()
    assert 'forget_factory_reset]' in src and 'reset.forget_done(BOOT)' in src
    ok('the record of a reset is forgotten every boot once its request is gone (epilykos-wifi-import)')

    # -- epilykos-factory-reset on the console ---------------------------------------
    calls = d / 'systemctl.log'
    fake = d / 'systemctl'
    fake.write_text(f'#!/bin/sh\necho "$@" >> {calls}\n')
    fake.chmod(0o755)
    env = dict(os.environ, EPILYKOS_SYSTEMCTL=str(fake), PYTHONPATH=str(PYLIB), PYTHONDONTWRITEBYTECODE='1')
    console = [sys.executable, str(OVERLAY / 'usr/bin/epilykos-factory-reset')]
    r = subprocess.run(console, input='no\n', env=env, capture_output=True, text=True)
    assert r.returncode == 1 and 'nothing erased' in r.stderr and not (boot / 'factory-reset.txt').exists()
    assert not calls.exists()
    r = subprocess.run(console, input='ERASE\n', env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert reset.pending() and b'the console' in (boot / 'factory-reset.txt').read_bytes()
    assert calls.read_text().splitlines() == ['reboot']
    (boot / 'factory-reset.txt').unlink()
    ok('epilykos-factory-reset: refuses without ERASE; with it, leaves the request and restarts')

    # -- epilykos-factory-reset-boot on an ext4 image ------------------------------------
    if not shutil.which('mke2fs') or not shutil.which('debugfs'):
        print('skip - epilykos-factory-reset-boot: mke2fs/debugfs not installed')
    else:
        img, seed, run = d / 'data.img', d / 'seed', d / 'run'
        run.mkdir()
        (seed / 'epilykos/app-data').mkdir(parents=True)
        (seed / 'epilykos/app-data/energy.db').write_text('years of readings')
        (seed / 'iwd').mkdir()
        (seed / 'iwd/Home.psk').write_text('[Security]\nPassphrase=secret\n')
        (seed / 'timesync').mkdir()
        (seed / 'timesync/clock').write_text('')
        os.utime(seed / 'timesync/clock', (1577836800, 1577836800))  # 2020: never moves this machine's clock

        def make_data(extra=None):
            if extra:
                (seed / reset.DONE_FILE).write_text(extra)
            elif (seed / reset.DONE_FILE).exists():
                (seed / reset.DONE_FILE).unlink()
            subprocess.run(['mke2fs', '-q', '-F', '-t', 'ext4', '-L', 'DATA', '-d', str(seed), str(img), '32M'], check=True)

        def listing():
            out = subprocess.run(['debugfs', '-R', 'ls -l /', str(img)], capture_output=True, text=True).stdout
            return {line.split()[-1] for line in out.splitlines() if line.split()}

        def boot_step():
            env = dict(os.environ, EPILYKOS_DATA_DEV=str(img), EPILYKOS_RUN_DIR=str(run))
            return subprocess.run(['sh', str(BOOT_STEP)], env=env, capture_output=True, text=True)

        request = boot / 'factory-reset.txt'
        make_data()
        r = boot_step()
        assert r.returncode == 0 and r.stdout == '' and {'epilykos', 'iwd'} <= listing()
        ok('boot: no request, DATA untouched')

        request.write_text('please reset this box\n')
        r = boot_step()
        assert r.returncode == 0 and 'no line saying ERASE' in r.stdout and request.exists()
        assert {'epilykos', 'iwd'} <= listing()
        ok('boot: a request without ERASE erases nothing and is left in place')

        request.write_bytes('﻿Erase\r\n'.encode())
        r = boot_step()
        assert r.returncode == 0, r.stdout + r.stderr
        assert 'DATA erased' in r.stdout and not request.exists()
        left = listing() - {'.', '..', 'lost+found', reset.DONE_FILE}
        assert not left, left
        label = subprocess.run(['dumpe2fs', '-h', str(img)], capture_output=True, text=True).stdout
        assert 'Filesystem volume name:   DATA' in label
        assert 'secret' not in r.stdout + r.stderr
        ok('boot: ERASE writes a new, empty DATA (still labelled DATA) and deletes the request')

        # The record of carrying a request out needs DATA mounted, so as root only.
        mnt = run / 'probe'
        mnt.mkdir()
        can_mount = os.geteuid() == 0 and subprocess.run(['mount', '-o', 'ro', str(img), str(mnt)],
                                                         capture_output=True).returncode == 0
        if can_mount:
            subprocess.run(['umount', str(mnt)], check=True)
            text = b'ERASE\r\nasked for from Epilykos settings\r\n'
            assert reset.DONE_FILE in listing(), 'the new DATA records the request it carried out'
            make_data(extra=hashlib.sha256(text).hexdigest() + '\n')
            request.write_bytes(text)
            r = boot_step()
            assert r.returncode == 0 and 'already carried out' in r.stdout and not request.exists()
            assert {'epilykos', 'iwd'} <= listing(), 'a set-up-again box is not erased a second time'
            ok('boot: a request already carried out (it could not be deleted) is not carried out again')
        else:
            print('skip - boot: already-carried-out guard (needs root to mount the image)')

    src = BOOT_STEP.read_text()
    unit = (OVERLAY / 'usr/lib/systemd/system/epilykos-factory-reset.service').read_text()
    assert 'Before=data.mount' in unit and 'After=' in unit and 'epilykos-data-grow.service' in unit
    assert 'ConditionPathExists=/boot/factory-reset.txt' in unit
    preset = (OVERLAY / 'usr/lib/systemd/system-preset/50-epilykos.preset').read_text()
    assert 'enable epilykos-factory-reset.service' in preset
    assert src.index('mke2fs') < src.rindex('delete_request\n'), 'the request goes only after DATA is rewritten'
    ok('unit: before DATA is mounted, after it is grown, only when asked; enabled')

print('\nPASS factory reset self-tests')
