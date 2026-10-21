"""SSH maintenance login for EpilykosOS (C-ACCESS-001, D-ACCESS-001).

SSH is off on a fresh box. When it is on, user `epilykos` (with sudo) logs
in with a password and/or public keys; root login is never allowed. All of
it lives on DATA so it survives OS updates and slot switches:

  /data/ssh/enabled              present = sshd runs
  /data/ssh/password.hash        crypt(3) hash of the epilykos password
  /data/ssh/authorized_keys      one public key per line
  /data/ssh/ssh_host_ed25519_key the box's host key (made on first start)

The root filesystem is read-only, so the password reaches sshd through a
copy of /etc/shadow in /run with the epilykos entry filled in, bind-mounted
over /etc/shadow at boot (epilykos-ssh shadow). The copy is rebuilt from
the image's own /etc/shadow each boot, so an OS update's accounts are kept.

Nothing here logs a password or hash.
"""
import base64
import ctypes
import ctypes.util
import hashlib
import os
import re
import secrets
import string

SSH_DIR = os.environ.get('EPILYKOS_SSH_DIR', '/data/ssh')
RUN_DIR = os.environ.get('EPILYKOS_RUN_DIR', '/run/epilykos')
USER = 'epilykos'

KEY_TYPES = {
    'ssh-ed25519', 'ssh-rsa', 'ecdsa-sha2-nistp256', 'ecdsa-sha2-nistp384', 'ecdsa-sha2-nistp521',
    'sk-ssh-ed25519@openssh.com', 'sk-ecdsa-sha2-nistp256@openssh.com',
}
# Hashes Raspberry Pi Imager and libxcrypt produce: sha256crypt, sha512crypt,
# yescrypt, bcrypt. Nothing weaker is accepted from a file.
HASH_RE = re.compile(r'^\$(5|6|y|2b)\$[./0-9A-Za-z$=,]{8,}$')


class SshError(ValueError):
    """Unusable SSH settings. The message never contains a password."""


def _path(name, ssh_dir=None):
    return os.path.join(ssh_dir or SSH_DIR, name)


def _write_private(path, text):
    os.makedirs(os.path.dirname(path), mode=0o755, exist_ok=True)
    tmp = path + '.tmp'
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


# -- on/off ---------------------------------------------------------------------

def is_enabled(ssh_dir=None):
    return os.path.exists(_path('enabled', ssh_dir))


def set_enabled(on, ssh_dir=None):
    path = _path('enabled', ssh_dir)
    if on:
        os.makedirs(os.path.dirname(path), mode=0o755, exist_ok=True)
        open(path, 'a').close()
    elif os.path.exists(path):
        os.unlink(path)


# -- password -------------------------------------------------------------------

# The image's libxcrypt is built without the glibc-compatible API, so it is
# only libcrypt.so.2; find_library() finds nothing there (no ldconfig cache
# or compiler). Distributions ship libcrypt.so.1.
LIBCRYPT_NAMES = ('libcrypt.so.2', 'libcrypt.so.1')
_free = ctypes.CDLL(None).free  # crypt_ra's buffer is malloc'd for the caller
_free.argtypes = [ctypes.c_void_p]


def _libcrypt():
    names = [n for n in (ctypes.util.find_library('crypt'),) if n] + list(LIBCRYPT_NAMES)
    for name in names:
        try:
            lib = ctypes.CDLL(name)
            break
        except OSError:
            continue
    else:
        raise OSError(f'no libcrypt ({", ".join(LIBCRYPT_NAMES)})')
    lib.crypt_ra.restype = ctypes.c_char_p
    lib.crypt_ra.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.POINTER(ctypes.c_void_p),
                             ctypes.POINTER(ctypes.c_int)]
    return lib


def hash_password(password):
    """sha512crypt via libxcrypt (Python 3.13 has no crypt module)."""
    validate_password(password)
    lib = _libcrypt()
    salt = '$6$' + ''.join(secrets.choice(string.ascii_letters + string.digits + './') for _ in range(16))
    data, size = ctypes.c_void_p(), ctypes.c_int(0)
    try:
        out = lib.crypt_ra(password.encode(), salt.encode(), ctypes.byref(data), ctypes.byref(size))
        if not out or not out.startswith(b'$6$'):
            raise SshError('could not hash the password')
        return out.decode()
    finally:
        if data.value:
            _free(data)


def validate_password(password):
    if not isinstance(password, str) or not 8 <= len(password) <= 128:
        raise SshError('the SSH password must be 8 to 128 characters')
    if any(ord(c) < 32 or ord(c) == 127 for c in password):
        raise SshError('the SSH password must not contain control characters')


def validate_hash(value):
    value = (value or '').strip()
    if not HASH_RE.match(value) or ':' in value:
        raise SshError('unsupported password hash (sha256crypt, sha512crypt, yescrypt or bcrypt only)')
    return value


def password_set(ssh_dir=None):
    return os.path.exists(_path('password.hash', ssh_dir))


def set_password_hash(value, ssh_dir=None):
    _write_private(_path('password.hash', ssh_dir), validate_hash(value) + '\n')


def clear_password(ssh_dir=None):
    path = _path('password.hash', ssh_dir)
    if os.path.exists(path):
        os.unlink(path)


def password_hash(ssh_dir=None):
    try:
        with open(_path('password.hash', ssh_dir)) as f:
            return validate_hash(f.read())
    except (OSError, SshError):
        return None


def render_shadow(base, pw_hash):
    """/etc/shadow text with the epilykos password field set; '*' (no
    password, but not locked, so key logins still work) without one."""
    field = pw_hash or '*'
    lines, found = [], False
    for line in base.splitlines():
        parts = line.split(':')
        if parts and parts[0] == USER and len(parts) >= 2:
            parts[1] = field
            line = ':'.join(parts)
            found = True
        lines.append(line)
    if not found:
        lines.append(f'{USER}:{field}:19000:0:99999:7:::')
    return '\n'.join(lines) + '\n'


def write_shadow(ssh_dir=None, run_dir=None):
    """Rewrite /run/epilykos/shadow in place (it is bind-mounted over
    /etc/shadow, so the file must keep its inode)."""
    run_dir = run_dir or RUN_DIR
    with open(os.path.join(run_dir, 'shadow.base')) as f:
        text = render_shadow(f.read(), password_hash(ssh_dir))
    path = os.path.join(run_dir, 'shadow')
    fd = os.open(path, os.O_WRONLY | os.O_CREAT, 0o600)
    with os.fdopen(fd, 'w') as f:
        f.truncate(0)
        f.write(text)
        f.flush()
        os.fsync(f.fileno())


# -- public keys ----------------------------------------------------------------

def parse_key(line):
    """(type, blob, comment) of one OpenSSH public key line."""
    parts = (line or '').strip().split(None, 2)
    if len(parts) < 2 or parts[0] not in KEY_TYPES:
        raise SshError('not an SSH public key (expected e.g. "ssh-ed25519 AAAA... name")')
    try:
        blob = base64.b64decode(parts[1], validate=True)
    except ValueError:
        raise SshError('the key is not valid base64') from None
    n = int.from_bytes(blob[:4], 'big') if len(blob) >= 4 else -1
    if n < 0 or blob[4:4 + n].decode('ascii', 'replace') != parts[0]:
        raise SshError('the key data does not match its type')
    comment = parts[2].strip() if len(parts) > 2 else ''
    if any(ord(c) < 32 for c in comment):
        raise SshError('the key comment contains control characters')
    return parts[0], blob, comment


def fingerprint(blob):
    return 'SHA256:' + base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip('=')


def _read_keys(ssh_dir=None):
    try:
        with open(_path('authorized_keys', ssh_dir)) as f:
            lines = [l.strip() for l in f]
    except OSError:
        return []
    keys = []
    for line in lines:
        if not line or line.startswith('#'):
            continue
        try:
            keys.append((line,) + parse_key(line))
        except SshError:
            continue
    return keys


def list_keys(ssh_dir=None):
    return [{'type': t, 'fingerprint': fingerprint(b), 'comment': c} for _, t, b, c in _read_keys(ssh_dir)]


def _write_keys(lines, ssh_dir=None):
    path = _path('authorized_keys', ssh_dir)
    os.makedirs(os.path.dirname(path), mode=0o755, exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w') as f:
        f.write(''.join(l + '\n' for l in lines))
        f.flush()
        os.fsync(f.fileno())  # a power cut must not leave an empty file and no way in
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)


def add_key(line, ssh_dir=None):
    ktype, blob, comment = parse_key(line)
    fp = fingerprint(blob)
    keys = _read_keys(ssh_dir)
    if any(fingerprint(b) == fp for _, _, b, _ in keys):
        return fp
    clean = f'{ktype} {base64.b64encode(blob).decode()}' + (f' {comment}' if comment else '')
    _write_keys([k[0] for k in keys] + [clean], ssh_dir)
    return fp


def remove_key(fp, ssh_dir=None):
    keys = _read_keys(ssh_dir)
    kept = [k[0] for k in keys if fingerprint(k[2]) != fp]
    if len(kept) == len(keys):
        raise SshError('no such key')
    _write_keys(kept, ssh_dir)


def status(ssh_dir=None):
    return {'enabled': is_enabled(ssh_dir), 'password_set': password_set(ssh_dir), 'keys': list_keys(ssh_dir),
            'user': USER}


# -- Files on BOOT --------------------------------------------------------------

_KEY_IN_TEXT = re.compile(r'(?<![\w-])((?:ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp(?:256|384|521)|sk-ssh-ed25519@openssh\.com'
                          r'|sk-ecdsa-sha2-nistp256@openssh\.com) [A-Za-z0-9+/]+={0,3}(?: [^\'"\n\\\\]*)?)')
_USERCONF = re.compile(r"userconf(?:-pi/userconf)?\s+'[^']*'\s+'(\$[^']+)'")
_CHPASSWD = re.compile(r"""["']?\$FIRSTUSER:["']?\s*'(\$[^']+)'\s*\|\s*chpasswd\s+-e""")


def parse_firstrun_ssh(text):
    """SSH settings from Raspberry Pi Imager's firstrun.sh: (enabled, password
    hash or None, [public keys]). Imager's "public-key only" mode lists the
    keys after enable_ssh -k and turns password logins off, so the user's
    password hash is only taken in password mode. Nothing is executed."""
    enabled = bool(re.search(r'imager_custom\s+enable_ssh\b|systemctl\s+enable\s+ssh\b', text))
    if not enabled:
        return False, None, []
    keys = []
    for m in _KEY_IN_TEXT.finditer(text):
        line = m.group(1).strip()
        try:
            parse_key(line)
        except SshError:
            continue
        if line not in keys:
            keys.append(line)
    key_only = bool(re.search(r'enable_ssh\s+-k\b', text)) or 'PasswordAuthentication no' in text
    pw_hash = None
    if not key_only:
        m = _USERCONF.search(text) or _CHPASSWD.search(text)
        if m:
            try:
                pw_hash = validate_hash(m.group(1))
            except SshError:
                pw_hash = None
    return True, pw_hash, keys


def parse_authorized_keys(text):
    """Valid public keys from an authorized_keys file dropped on BOOT."""
    keys = []
    for line in text.splitlines():
        line = line.strip().lstrip('﻿')
        if not line or line.startswith('#'):
            continue
        parse_key(line)  # one bad line rejects the file, so nothing is half-applied
        keys.append(line)
    if not keys:
        raise SshError('no public keys in the file')
    return keys
