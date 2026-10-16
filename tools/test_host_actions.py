#!/usr/bin/env python3
"""Self-tests for the dashboard's system actions on the host (C-HOST-001)
and the SSH maintenance login (C-ACCESS-001): epilykos_ssh, epilykos-hostd
over its real Unix socket (with a fake systemctl), and SSH settings
imported from BOOT by epilykos-wifi-import. Passwords never reach a log.
"""
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.dont_write_bytecode = True  # keep __pycache__ out of the rootfs overlay
ROOT = Path(__file__).resolve().parent.parent
OVERLAY = ROOT / 'os/board/epilykos/raspberrypi3/rootfs-overlay'
PYLIB = OVERLAY / 'usr/lib/epilykos/python'
sys.path.insert(0, str(PYLIB))
import epilykos_ssh as ssh  # noqa: E402

SECRET = 'correct horse battery'
KEY = 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl fola@laptop'
KEY2 = ('ecdsa-sha2-nistp256 AAAAE2VjZHNhLXNoYTItbmlzdHAyNTYAAAAIbmlzdHAyNTYAAABBBEmKSENjQEezOmxkZMy7opKgwFB9nkt5YRrYMjNu'
        'G5N87uRgg6CLrbo5wAdT/y6v0mKV0U2w0WZ2YB/++Tpockg=')


def ok(name):
    print(f'ok - {name}')


def expect(exc, fn, *args):
    try:
        fn(*args)
    except exc as e:
        assert SECRET not in str(e)
        return
    raise AssertionError(f'{fn.__name__}: expected {exc.__name__}')


# -- epilykos_ssh -----------------------------------------------------------------
h = ssh.hash_password(SECRET)
assert h.startswith('$6$') and SECRET not in h
import ctypes  # noqa: E402
lib = ctypes.CDLL('libcrypt.so.1')
lib.crypt.restype = ctypes.c_char_p
assert lib.crypt(SECRET.encode(), h.encode()).decode() == h, 'hash must verify with crypt(3)'
expect(ssh.SshError, ssh.hash_password, 'short')
expect(ssh.SshError, ssh.validate_hash, '$1$md5$weak')
expect(ssh.SshError, ssh.validate_hash, 'plaintext')
ok('password: sha512crypt that crypt(3) verifies; short passwords and weak or plain hashes refused')

t, blob, comment = ssh.parse_key(KEY)
assert (t, comment) == ('ssh-ed25519', 'fola@laptop')
assert ssh.fingerprint(blob) == 'SHA256:' + subprocess.run(
    ['python3', '-c', 'import base64,hashlib,sys;print(base64.b64encode(hashlib.sha256(base64.b64decode(sys.argv[1])).digest()).decode().rstrip("="))',
     KEY.split()[1]], capture_output=True, text=True).stdout.strip()
expect(ssh.SshError, ssh.parse_key, 'ssh-ed25519 notbase64!!')
expect(ssh.SshError, ssh.parse_key, 'ssh-rsa ' + KEY.split()[1])  # blob says ed25519
expect(ssh.SshError, ssh.parse_key, 'ssh-dss AAAAB3NzaC1kc3M=')
ok('public keys: type, base64 and embedded type checked; SHA256 fingerprints')

base = 'root::19000:0:99999:7:::\nsshd:*:19000:0:99999:7:::\nepilykos:*:19000:0:99999:7:::\n'
assert ssh.render_shadow(base, h).splitlines()[2] == f'epilykos:{h}:19000:0:99999:7:::'
assert ssh.render_shadow(base, None).splitlines()[2] == 'epilykos:*:19000:0:99999:7:::'
assert ssh.render_shadow(base, None).splitlines()[0] == 'root::19000:0:99999:7:::'
assert ssh.render_shadow('root::1::::::\n', h).splitlines()[1].startswith(f'epilykos:{h}:')
ok('shadow: only the epilykos entry changes; "*" (keys only, not locked) without a password')

FIRSTRUN_KEYS = f"""#!/bin/bash
FIRSTUSER=`getent passwd 1000 | cut -d: -f1`
if [ -f /usr/lib/raspberrypi-sys-mods/imager_custom ]; then
   /usr/lib/raspberrypi-sys-mods/imager_custom enable_ssh -k '{KEY}' '{KEY2}'
else
   install -o "$FIRSTUSER" -m 700 -d "$FIRSTUSERHOME/.ssh"
   install -o "$FIRSTUSER" -m 600 <(printf "{KEY}\\n{KEY2}") "$FIRSTUSERHOME/.ssh/authorized_keys"
   echo 'PasswordAuthentication no' >>/etc/ssh/sshd_config
   systemctl enable ssh
fi
if [ -f /usr/lib/userconf-pi/userconf ]; then
   /usr/lib/userconf-pi/userconf 'fola' '{h}'
else
   echo "$FIRSTUSER:"'{h}' | chpasswd -e
fi
"""
FIRSTRUN_PASSWORD = f"""#!/bin/bash
if [ -f /usr/lib/raspberrypi-sys-mods/imager_custom ]; then
   /usr/lib/raspberrypi-sys-mods/imager_custom enable_ssh
else
   systemctl enable ssh
fi
if [ -f /usr/lib/userconf-pi/userconf ]; then
   /usr/lib/userconf-pi/userconf 'fola' '{h}'
else
   echo "$FIRSTUSER:"'{h}' | chpasswd -e
fi
"""
assert ssh.parse_firstrun_ssh(FIRSTRUN_KEYS) == (True, None, [KEY, KEY2])
assert ssh.parse_firstrun_ssh(FIRSTRUN_PASSWORD) == (True, h, [])
assert ssh.parse_firstrun_ssh(FIRSTRUN_PASSWORD.replace("/usr/lib/userconf-pi/userconf 'fola'", 'true #')) == (True, h, [])
assert ssh.parse_firstrun_ssh('#!/bin/bash\necho hi\n') == (False, None, [])
ok('Imager firstrun.sh: key-only mode takes the keys, password mode the hash, no SSH section turns nothing on')

assert ssh.parse_authorized_keys(f'# mine\n{KEY}\r\n\n{KEY2}\n') == [KEY, KEY2]
expect(ssh.SshError, ssh.parse_authorized_keys, f'{KEY}\nnot a key\n')
expect(ssh.SshError, ssh.parse_authorized_keys, '# nothing\n')
ok('authorized_keys on BOOT: all keys or nothing')

# -- epilykos-hostd over its socket --------------------------------------------------
with tempfile.TemporaryDirectory() as d:
    d = Path(d)
    sock, run, sshdir, calls = d / 'host/host.sock', d / 'run', d / 'ssh', d / 'systemctl.log'
    run.mkdir()
    (run / 'shadow.base').write_text(base)
    (run / 'shadow').write_text('')
    fake = d / 'systemctl'
    fake.write_text(f'#!/bin/sh\necho "$@" >> {calls}\n')
    fake.chmod(0o755)
    env = dict(os.environ, EPILYKOS_HOST_SOCKET=str(sock), EPILYKOS_RUN_DIR=str(run), EPILYKOS_SSH_DIR=str(sshdir),
               EPILYKOS_SYSTEMCTL=str(fake), EPILYKOS_APP_GID=str(os.getgid()),
               PYTHONPATH=str(PYLIB), PYTHONDONTWRITEBYTECODE='1')
    proc = subprocess.Popen([sys.executable, str(OVERLAY / 'usr/bin/epilykos-hostd')], env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        for _ in range(100):
            if sock.exists():
                break
            time.sleep(0.05)
        assert oct(sock.stat().st_mode & 0o777) == '0o660', oct(sock.stat().st_mode)

        def call(req, raw=None):
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                s.connect(str(sock))
                s.sendall(raw if raw is not None else json.dumps(req).encode() + b'\n')
                return json.loads(s.makefile().readline())

        st = call({'action': 'status'})
        assert st['ok'] and st['appliance'] and st['ssh'] == {'enabled': False, 'password_set': False, 'keys': [],
                                                             'user': 'epilykos'} and st['wifi_setup_until'] == 0, st
        ok('hostd status: SSH off, no password, no keys, no Wi-Fi setup window')

        r = call({'action': 'wifi_setup'})
        assert r['ok'] and r['wifi_setup_until'] > time.time() + 890
        assert float((run / 'wifi-setup-until').read_text()) == r['wifi_setup_until']
        assert call({'action': 'status'})['wifi_setup_until'] == r['wifi_setup_until']
        ok('hostd wifi_setup: opens Bluetooth setup for 15 minutes')

        r = call({'action': 'ssh_enable'})
        assert r['ok'] and r['ssh']['enabled'] and (sshdir / 'enabled').exists()
        r = call({'action': 'ssh_set_password', 'password': SECRET})
        assert r['ok'] and r['ssh']['password_set']
        line = [l for l in (run / 'shadow').read_text().splitlines() if l.startswith('epilykos:')][0]
        assert lib.crypt(SECRET.encode(), line.split(':')[1].encode()).decode() == line.split(':')[1]
        assert oct((sshdir / 'password.hash').stat().st_mode & 0o777) == '0o600'
        r = call({'action': 'ssh_set_password', 'password': 'short'})
        assert not r['ok'] and '8 to 128' in r['error']
        ok('hostd SSH: enable, set a password (into the shadow copy, 0600 on DATA), short password refused')

        r = call({'action': 'ssh_add_key', 'key': KEY})
        fp = r['ssh']['keys'][0]['fingerprint']
        assert r['ok'] and r['ssh']['keys'] == [{'type': 'ssh-ed25519', 'fingerprint': fp, 'comment': 'fola@laptop'}]
        assert call({'action': 'ssh_add_key', 'key': KEY})['ssh']['keys'] == r['ssh']['keys'], 'no duplicates'
        assert not call({'action': 'ssh_add_key', 'key': 'garbage'})['ok']
        r = call({'action': 'ssh_remove_key', 'fingerprint': fp})
        assert r['ok'] and r['ssh']['keys'] == []
        assert not call({'action': 'ssh_remove_key', 'fingerprint': fp})['ok']
        r = call({'action': 'ssh_clear_password'})
        assert r['ok'] and not r['ssh']['password_set']
        assert 'epilykos:*:' in (run / 'shadow').read_text()
        r = call({'action': 'ssh_disable'})
        assert r['ok'] and not r['ssh']['enabled']
        ok('hostd SSH: add (no duplicates), remove by fingerprint, clear password, disable')

        assert call({'action': 'rm -rf /'}) == {'ok': False, 'error': 'unknown action'}
        assert call(None, raw=b'not json\n') == {'ok': False, 'error': 'malformed request'}
        assert call(None, raw=b'[1,2]\n') == {'ok': False, 'error': 'malformed request'}
        assert call(None, raw=b'{"action":"status","pad":"' + b'x' * 20000 + b'"}\n')['error'] == 'request too large'
        ok('hostd refuses unknown actions, malformed and oversized requests')

        assert call({'action': 'restart'}) == {'ok': True}
        assert call({'action': 'shutdown'}) == {'ok': True}
        time.sleep(1.5)
        lines = calls.read_text().splitlines()
        assert '--no-block start epilykos-sshd.service' in lines and '--no-block stop epilykos-sshd.service' in lines
        assert lines[-2:] in (['--no-block reboot', '--no-block poweroff'], ['--no-block poweroff', '--no-block reboot']), lines
        ok('hostd restart/shutdown: reply first, then systemctl reboot/poweroff')
    finally:
        proc.terminate()
        out = proc.communicate(timeout=5)[0]
    assert SECRET not in out and '$6$' not in out, 'hostd log leaks a password or hash:\n' + out
    ok('hostd never logs a password or hash')

# -- SSH from BOOT (epilykos-wifi-import) ---------------------------------------------
for name, files, expect_keys, expect_pw in [
        ('Imager key-only', {'firstrun.sh': FIRSTRUN_KEYS}, 2, False),
        ('Imager password', {'firstrun.sh': FIRSTRUN_PASSWORD}, 0, True),
        ('authorized_keys', {'authorized_keys': KEY + '\n'}, 1, False)]:
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        boot, run, sshdir = d / 'boot', d / 'run', d / 'ssh'
        boot.mkdir()
        run.mkdir()
        (run / 'shadow.base').write_text(base)
        (run / 'shadow').write_text('')
        for fname, text in files.items():
            (boot / fname).write_text(text)
        env = dict(os.environ, EPILYKOS_BOOT_DIR=str(boot), EPILYKOS_IWD_DIR=str(d / 'iwd'), EPILYKOS_RUN_DIR=str(run),
                   EPILYKOS_SSH_DIR=str(sshdir), EPILYKOS_WIFI_COUNTRY_FILE=str(d / 'country'),
                   PYTHONPATH=str(PYLIB), PYTHONDONTWRITEBYTECODE='1')
        r = subprocess.run([sys.executable, str(OVERLAY / 'usr/bin/epilykos-wifi-import')], env=env,
                           capture_output=True, text=True)
        out = r.stdout + r.stderr
        assert r.returncode == 0, out
        st = ssh.status(str(sshdir))
        assert st['enabled'] and len(st['keys']) == expect_keys and st['password_set'] == expect_pw, (name, st)
        assert ('epilykos:$' in (run / 'shadow').read_text()) == expect_pw
        assert os.listdir(boot) == [], (name, os.listdir(boot))
        assert h not in out and SECRET not in out
ok('BOOT import: Imager key-only and password modes and authorized_keys turn SSH on; files removed; nothing logged')

print('\nPASS host action self-tests')
