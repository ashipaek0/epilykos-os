"""Factory reset: erase everything kept on DATA, so the box starts again as
it was first flashed (C-HOST-001). Three ways to ask, all of which leave
the same file on BOOT:

  Settings > System > Factory reset    hostd's factory_reset action
  sudo epilykos-factory-reset          on the console, or over SSH
  factory-reset.txt on BOOT            ERASE on a line of its own, written
                                       from any computer (the card's BOOT
                                       partition is FAT)

The erasing is done early in the next boot by epilykos-factory-reset-boot,
before DATA is mounted: a new, empty filesystem is written over DATA, so
none of it is left: the app's data, settings, password and backups, the
known Wi-Fi networks and Wi-Fi country, the time zone, the dashboard port,
SSH settings and keys, Bluetooth pairings, the logs, and application
updates installed from settings (the app comes back as this OS release
shipped it). Then the request is deleted. What is not on DATA stays: both
OS slots and which one is in use, and the machine ID.

ERASE is asked for every way, so a stray or empty file erases nothing.
"""
import os
import subprocess
import time

REQUEST_FILE = 'factory-reset.txt'
CONFIRM = 'ERASE'
BOOT = os.environ.get('EPILYKOS_BOOT_DIR', '/boot')
DATA = os.environ.get('EPILYKOS_DATA_DIR', '/data')
# Written on the new DATA by epilykos-factory-reset-boot: the request it
# carried out, so one it could not delete from BOOT isn't carried out again.
DONE_FILE = '.factory-reset-done'


class ResetError(ValueError):
    """The reset was not asked for properly; nothing is erased."""


def confirmed(data):
    """True when the request file says ERASE on a line of its own (any case,
    surrounding spaces, Windows line endings or a byte-order mark allowed)."""
    if isinstance(data, bytes):
        data = data.decode('utf-8', 'replace')
    return any(line.strip().upper() == CONFIRM for line in data.lstrip('﻿').splitlines())


def check(confirm):
    if not isinstance(confirm, str) or confirm.strip().upper() != CONFIRM:
        raise ResetError(f'type {CONFIRM} to confirm the factory reset')


def boot_writable(writable, boot=None):
    """BOOT is mounted read-only; remount it for the write (tests use a plain directory)."""
    if os.environ.get('EPILYKOS_BOOT_DIR'):
        return
    opt = 'remount,rw' if writable else 'remount,ro'
    subprocess.run(['mount', '-o', opt, boot or BOOT], check=writable)


def request(source, boot=None):
    """Leave the request on BOOT; the next boot erases DATA."""
    boot = boot or BOOT
    path = os.path.join(boot, REQUEST_FILE)
    when = time.strftime('%Y-%m-%d %H:%M %Z')
    text = (f'{CONFIRM}\n'
            f'Factory reset asked for from {source} on {when}.\n'
            'EpilykosOS erases everything on its DATA partition the next time it starts,\n'
            'then deletes this file. To cancel before then, delete this file.\n')
    boot_writable(True, boot)
    try:
        tmp = path + '.tmp'
        with open(tmp, 'w', newline='\r\n') as f:  # readable in Notepad too
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        dirfd = os.open(boot, os.O_RDONLY)
        try:
            os.fsync(dirfd)
        finally:
            os.close(dirfd)
    finally:
        boot_writable(False, boot)
    return path


def pending(boot=None):
    path = os.path.join(boot or BOOT, REQUEST_FILE)
    try:
        with open(path, 'rb') as f:
            return confirmed(f.read())
    except OSError:
        return False


def forget_done(boot=None, data=None):
    """Once the request is gone from BOOT, the record of carrying it out is
    no longer needed (and must go, or an identical request later would be
    taken as already done). epilykos-wifi-import calls this every boot."""
    done = os.path.join(data or DATA, DONE_FILE)
    if os.path.exists(done) and not os.path.exists(os.path.join(boot or BOOT, REQUEST_FILE)):
        os.remove(done)
        return True
    return False
