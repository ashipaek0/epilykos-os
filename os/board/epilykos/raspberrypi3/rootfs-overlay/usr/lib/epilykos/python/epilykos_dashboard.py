"""Reset the Epilykos dashboard password from the host, for an
administrator who has forgotten it. Both ways need physical access to the
box or its card:

  dashboard-password.txt on BOOT   read at boot by epilykos-wifi-import
  epilykos-reset-password          on the console, or over SSH with sudo

The app keeps its password in /data/epilykos/app-data/settings-password as
"<salt>:<hash>", where hash = scrypt(password, salt, 64 bytes) with Node's
defaults (N=16384, r=8, p=1) and salt is 16 random bytes in hex, used as a
string (modules/sessionAuth.js). The app reads it at start, so Epilykos is
restarted, or not yet started, when it changes. Never logged.

Either reset also switches two-step sign-in off (the app's two-step.json next
to the password file), so a lost phone is never a lock-out.
"""
import hashlib
import os
import secrets

PASSWORD_FILE = os.environ.get('EPILYKOS_DASHBOARD_PASSWORD_FILE', '/data/epilykos/app-data/settings-password')
APP_UID = APP_GID = 1000
BOOT_FILE = 'dashboard-password.txt'
TWO_STEP_FILE = 'two-step.json'


class PasswordError(ValueError):
    """An unusable password. The message never contains it."""


def validate(password):
    if not isinstance(password, str) or not 4 <= len(password) <= 128:
        raise PasswordError('the dashboard password must be 4 to 128 characters')
    if any(ord(c) < 32 or ord(c) == 127 for c in password):
        raise PasswordError('the dashboard password must not contain control characters')
    return password


def hash_password(password, salt):
    return hashlib.scrypt(password.encode(), salt=salt.encode(), n=16384, r=8, p=1, dklen=64).hex()


def verify(password, path=None):
    with open(path or PASSWORD_FILE) as f:
        salt, _, stored = f.read().strip().partition(':')
    return secrets.compare_digest(hash_password(password, salt), stored)


def clear_two_step(path=None):
    """Remove the app's two-step sign-in state beside the password file.
    True when there was some."""
    try:
        os.unlink(os.path.join(os.path.dirname(path or PASSWORD_FILE), TWO_STEP_FILE))
        return True
    except FileNotFoundError:
        return False


def set_password(password, path=None):
    """Save a new password and switch two-step sign-in off. Returns True when
    two-step sign-in had been on."""
    path = path or PASSWORD_FILE
    validate(password)
    salt = secrets.token_hex(16)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.tmp'
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as f:
        f.write(f'{salt}:{hash_password(password, salt)}')
        f.flush()
        os.fsync(f.fileno())
    try:
        os.chown(tmp, APP_UID, APP_GID)  # the app runs as uid 1000
    except PermissionError:
        pass  # tests run unprivileged
    os.replace(tmp, path)
    return clear_two_step(path)


def parse_boot_file(text):
    """The password from dashboard-password.txt: its first non-empty line,
    without the spaces around it."""
    for line in text.lstrip('﻿').splitlines():
        if line.strip():
            return validate(line.strip())
    raise PasswordError('the file has no password in it')
