#!/usr/bin/env python3
"""Self-tests for the dashboard's system actions on the host (C-HOST-001)
and the SSH maintenance login (C-ACCESS-001): epilykos_ssh, epilykos-hostd
over its real Unix socket (with a fake systemctl), SSH settings imported
from BOOT by epilykos-wifi-import, the dashboard port (epilykos_port, and
the same rules in epilykos-run), and the dashboard password reset
(dashboard-password.txt on BOOT, epilykos-reset-password). Passwords never
reach a log.
"""
import json
import os
import shutil
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
import epilykos_dashboard as dashboard  # noqa: E402
import epilykos_port as port  # noqa: E402
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

# On the image libxcrypt is only libcrypt.so.2 and find_library() finds
# nothing (Pi 3B, 2026-10-17: "libcrypt.so.1: cannot open shared object file").
import ctypes.util  # noqa: E402
real_cdll, real_find, tried = ctypes.CDLL, ctypes.util.find_library, []


def image_cdll(name, *a, **kw):
    tried.append(name)
    if name != 'libcrypt.so.2':
        raise OSError(f'{name}: cannot open shared object file: No such file or directory')
    return real_cdll(real_find('crypt') or 'libcrypt.so.1', *a, **kw)


ctypes.CDLL, ctypes.util.find_library = image_cdll, lambda name: None
try:
    assert ssh.hash_password(SECRET).startswith('$6$') and tried == ['libcrypt.so.2']
    tried.clear()


    def no_cdll(name, *a, **kw):
        tried.append(name)
        raise OSError(f'{name}: cannot open shared object file')
    ctypes.CDLL = no_cdll
    expect(OSError, ssh.hash_password, SECRET)
    assert tried == ['libcrypt.so.2', 'libcrypt.so.1']
finally:
    ctypes.CDLL, ctypes.util.find_library = real_cdll, real_find
ok('password: libcrypt.so.2 is used when that is all the image has')

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

# -- dashboard port ------------------------------------------------------------------
for good in (80, 1024, 3000, 8080, 65535, '8080', ' 8443 '):
    assert port.validate(good) == int(str(good).strip()), good
for bad in (0, 22, 443, 1023, 65536, 5353, 5355, -1, True, None, '80x', '', 80.0):
    expect(port.PortError, port.validate, bad)
with tempfile.TemporaryDirectory() as d:
    f = os.path.join(d, 'epilykos', 'port')
    assert port.read(f) == 3000, 'unset: 3000'
    assert port.write(8080, f) == 8080 and open(f).read() == '8080\n' and port.read(f) == 8080
    for junk in ('22\n', 'abc\n', '', '5353\n'):
        open(f, 'w').write(junk)
        assert port.read(f) == 3000, junk
assert port.dashboard_url('192.0.2.10', 80) == 'http://192.0.2.10/'
assert port.dashboard_url('192.0.2.10', 8080) == 'http://192.0.2.10:8080/'
with socket.socket() as busy:
    busy.bind(('127.0.0.1', 0))
    busy.listen()
    taken = busy.getsockname()[1]
    assert port.in_use(taken), 'a listening port is in use'
assert not port.in_use(taken), 'a closed port is free'
ok('port: 80 or 1024-65535 (not mDNS/LLMNR), 3000 when unset or unreadable, in-use check, dashboard URL')

# epilykos-run must read the file with the same rules (it is sh, not Python).
run_sh = (OVERLAY / 'usr/bin/epilykos-run').read_text()
block = run_sh[run_sh.index('PORT=3000\n'):]
block = block[:block.index('\nfi\n') + 4]
with tempfile.TemporaryDirectory() as d:
    f = os.path.join(d, 'port')
    for content, want in (('8080\n', '8080'), ('80\n', '80'), ('65535', '65535'), ('22\n', '3000'), ('0080\n', '3000'),
                          ('5353\n', '3000'), ('99999\n', '3000'), ('8080; reboot\n', '3000'), ('', '3000'), (None, '3000')):
        if content is None:
            os.path.exists(f) and os.unlink(f)
        else:
            open(f, 'w').write(content)
        got = subprocess.run(['sh', '-ec', block.replace('/data/epilykos/port', f) + 'echo "$PORT"'],
                             capture_output=True, text=True, check=True).stdout.strip()
        assert got == want, (content, got)
assert '--env PORT="${PORT}"' in run_sh and "127.0.0.1:${PORT}/healthz" in run_sh
ok('epilykos-run: same port rules, passes PORT to the app and its health check')

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
               EPILYKOS_PORT_FILE=str(d / 'data/port'), PYTHONPATH=str(PYLIB), PYTHONDONTWRITEBYTECODE='1',
               EPILYKOS_SYSTEM_DATA=str(d / 'data'), EPILYKOS_ZONEINFO=str(d / 'zoneinfo'),
               EPILYKOS_IW=str(d / 'iw'), EPILYKOS_MODPROBE='true', EPILYKOS_WIFI_COUNTRY_FILE=str(d / 'data/wifi-country'),
               EPILYKOS_PROC_ROUTE=str(d / 'route'), EPILYKOS_SYS_NET=str(d / 'sys-net'),
               EPILYKOS_BOOT_DIR=str(d / 'boot'))
    (d / 'boot').mkdir()
    (d / 'route').write_text('Iface\tDestination\tGateway\tFlags\nwlan0\t00000000\t0100A8C0\t0003\n')
    for zone in ('UTC', 'Africa/Lagos'):
        (d / 'zoneinfo' / zone).parent.mkdir(parents=True, exist_ok=True)
        (d / 'zoneinfo' / zone).write_bytes(b'TZif2 test zone')
    (d / 'zoneinfo/zone.tab').write_text('NG\t+0627+00324\tAfrica/Lagos\n')
    (d / 'iw').write_text(f'#!/bin/sh\necho "$@" >> {d / "iw-calls"}\n')
    (d / 'iw').chmod(0o755)
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
        assert st['network'] == {'gateway': '192.168.0.1', 'gateway_interface': 'wlan0', 'wifi': None,
                                 'wifi_country': None, 'wifi_country_source': None}, st['network']
        ok('hostd status: SSH off, no password, no keys, no Wi-Fi setup window; network (gateway, no Wi-Fi)')

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

        assert call({'action': 'status'})['port'] == 3000
        r = call({'action': 'set_port', 'port': 8080})
        assert r == {'ok': True, 'port': 8080}, r
        assert (d / 'data/port').read_text() == '8080\n' and call({'action': 'status'})['port'] == 8080
        assert call({'action': 'set_port', 'port': '8080'}) == {'ok': True, 'port': 8080}, 'unchanged: no restart'
        for bad in (22, 70000, 'x', None, 5353):
            r = call({'action': 'set_port', 'port': bad})
            assert not r['ok'] and 'port' in r['error'], (bad, r)
        with socket.socket() as busy:
            busy.bind(('0.0.0.0', 0))
            busy.listen()
            taken = busy.getsockname()[1]
            r = call({'action': 'set_port', 'port': taken})
            assert not r['ok'] and 'already in use' in r['error'], r
        assert call({'action': 'status'})['port'] == 8080
        time.sleep(1.5)
        assert calls.read_text().splitlines().count('--no-block restart epilykos.service') == 1
        ok('hostd set_port: saved on DATA, Epilykos restarted once; bad, reserved and busy ports refused')

        assert call({'action': 'status'})['clock']['timezone'] == 'UTC'
        assert call({'action': 'set_timezone', 'timezone': 'UTC'}) == {'ok': True, 'timezone': 'UTC', 'restart_required': False}
        r = call({'action': 'set_timezone', 'timezone': 'Africa/Lagos'})
        assert r == {'ok': True, 'timezone': 'Africa/Lagos', 'restart_required': True}, r
        assert call({'action': 'set_timezone', 'timezone': 'Africa/Lagos'})['restart_required'] is False
        for bad in ('../../etc/passwd', 'Mars/Olympus', 7):
            assert not call({'action': 'set_timezone', 'timezone': bad})['ok'], bad
        assert call({'action': 'status'})['clock']['timezone'] == 'Africa/Lagos'
        assert json.loads((run / 'clock.json').read_text())['timezone'] == 'Africa/Lagos'
        time.sleep(3.5)
        assert calls.read_text().splitlines().count('--no-block restart epilykos.service') == 2
        assert (d / 'iw-calls').read_text().splitlines() == ['reg set NG'], 'the Wi-Fi country follows the new zone, once'
        ok('hostd set_timezone: saved, clock state updated, Wi-Fi country set, Epilykos restarted only when the zone changes')

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

        request = d / 'boot/factory-reset.txt'
        for confirm in (None, '', 'yes', 'ERASE ALL'):
            r = call({'action': 'factory_reset', 'confirm': confirm} if confirm is not None else {'action': 'factory_reset'})
            assert r == {'ok': False, 'error': 'type ERASE to confirm the factory reset'}, r
        assert not request.exists()
        before = len(calls.read_text().splitlines())
        assert call({'action': 'factory_reset', 'confirm': ' erase '}) == {'ok': True}
        text = request.read_bytes()
        assert text.startswith(b'ERASE\r\n') and b'Epilykos settings' in text, text
        time.sleep(2.5)
        assert calls.read_text().splitlines()[before:] == ['--no-block reboot']
        ok('hostd factory_reset: only with ERASE; leaves factory-reset.txt on BOOT, then restarts')
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

# -- dashboard password reset -----------------------------------------------------------
NEW_PW = 'new dashboard pass'
NODE_VERIFY = ('const c=require("crypto"),[s,h]=require("fs").readFileSync(process.argv[1],"utf8").split(":");'
               'process.exit(c.timingSafeEqual(c.scryptSync(process.argv[2],s,64),Buffer.from(h,"hex"))?0:1)')


def app_accepts(path, password):
    """What the app's sessionAuth.verifyPassword computes, run by Node when it is here."""
    if not dashboard.verify(password, path):
        return False
    if shutil.which('node'):
        return subprocess.run(['node', '-e', NODE_VERIFY, path, password]).returncode == 0
    return True


with tempfile.TemporaryDirectory() as d:
    f = os.path.join(d, 'settings-password')
    dashboard.set_password(NEW_PW, f)
    assert oct(os.stat(f).st_mode & 0o777) == '0o600'
    assert app_accepts(f, NEW_PW) and not app_accepts(f, NEW_PW + 'x')
    for bad in ('abc', 'x' * 129, 'tab\there', None):
        expect(dashboard.PasswordError, dashboard.set_password, bad, f)
    assert dashboard.parse_boot_file('\ufeff\n  ' + NEW_PW + '  \r\nignored\n') == NEW_PW
    expect(dashboard.PasswordError, dashboard.parse_boot_file, '\n \n')
ok('dashboard password: stored as the app stores it (Node verifies it), 4-128 characters, first line of the file')

for label, data, replaced in [
        ('UTF-8', (NEW_PW + '\n').encode(), True),
        ('UTF-16 (Windows Notepad)', ('\ufeff' + NEW_PW + '\r\n').encode('utf-16-le'), True),
        ('blank (card pulled too soon)', b'', False),
        ('too short', b'abc\n', False)]:
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        boot = d / 'boot'
        boot.mkdir()
        (boot / 'dashboard-password.txt').write_bytes(data)
        pwfile = d / 'app-data/settings-password'
        env = dict(os.environ, EPILYKOS_BOOT_DIR=str(boot), EPILYKOS_IWD_DIR=str(d / 'iwd'), EPILYKOS_RUN_DIR=str(d),
                   EPILYKOS_SSH_DIR=str(d / 'ssh'), EPILYKOS_WIFI_COUNTRY_FILE=str(d / 'country'),
                   EPILYKOS_DASHBOARD_PASSWORD_FILE=str(pwfile), PYTHONPATH=str(PYLIB), PYTHONDONTWRITEBYTECODE='1')
        r = subprocess.run([sys.executable, str(OVERLAY / 'usr/bin/epilykos-wifi-import')], env=env,
                           capture_output=True, text=True)
        out = r.stdout + r.stderr
        assert r.returncode == 0 and NEW_PW not in out, out
        if replaced:
            assert app_accepts(str(pwfile), NEW_PW) and os.listdir(boot) == [], (label, out)
            assert 'dashboard password replaced' in out
        else:
            assert not pwfile.exists() and os.listdir(boot) == ['dashboard-password.txt'], (label, out)
            assert 'left in place' in out, out
ok('dashboard-password.txt on BOOT: replaces the password (UTF-8 or UTF-16) and is deleted; blank or short left in place')

with tempfile.TemporaryDirectory() as d:
    d = Path(d)
    calls = d / 'systemctl.log'
    fake = d / 'systemctl'
    fake.write_text(f'#!/bin/sh\necho "$@" >> {calls}\n')
    fake.chmod(0o755)
    pwfile = d / 'settings-password'
    env = dict(os.environ, EPILYKOS_DASHBOARD_PASSWORD_FILE=str(pwfile), EPILYKOS_SYSTEMCTL=str(fake),
               PYTHONPATH=str(PYLIB), PYTHONDONTWRITEBYTECODE='1')
    cmd = [sys.executable, str(OVERLAY / 'usr/bin/epilykos-reset-password')]
    r = subprocess.run(cmd, env=env, input=f'{NEW_PW}\nsomething else\n', capture_output=True, text=True)
    assert r.returncode == 1 and "don't match" in r.stderr and not pwfile.exists() and not calls.exists()
    r = subprocess.run(cmd, env=env, input='abc\nabc\n', capture_output=True, text=True)
    assert r.returncode == 1 and '4 to 128' in r.stderr and not pwfile.exists()
    r = subprocess.run(cmd, env=env, input=f'{NEW_PW}\n{NEW_PW}\n', capture_output=True, text=True)
    assert r.returncode == 0 and app_accepts(str(pwfile), NEW_PW), r.stderr
    assert calls.read_text().splitlines() == ['--no-block restart epilykos.service']
    assert NEW_PW not in r.stdout + r.stderr
ok('epilykos-reset-password: asks twice, refuses a mismatch or a short one, saves it and restarts Epilykos')

print('\nPASS host action self-tests')
