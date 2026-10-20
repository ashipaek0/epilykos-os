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
"""
import hashlib
import os
import secrets

PASSWORD_FILE = os.environ.get('EPILYKOS_DASHBOARD_PASSWORD_FILE', '/data/epilykos/app-data/settings-password')
APP_UID = APP_GID = 1000
BOOT_FILE = 'dashboard-password.txt'


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


def set_password(password, path=None):
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


def parse_boot_file(text):
    """The password from dashboard-password.txt: its first non-empty line,
    without the spaces around it."""
    for line in text.lstrip('﻿').splitlines():
        if line.strip():
            return validate(line.strip())
    raise PasswordError('the file has no password in it')
