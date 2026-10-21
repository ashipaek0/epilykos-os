"""Wi-Fi provisioning helpers shared by epilykos-wifi-import and epilykos-improv.

Wi-Fi is run by iwd; this module only writes iwd's known-network files
(one per network, in /data/iwd, which iwd sees as its state directory
/var/lib/iwd) and the regulatory country. iwd picks new files up by itself.

Passphrases are secrets (C-PROV-002): nothing here logs or prints one, and
error messages never include one.
"""
import os
import re
import tempfile
from dataclasses import dataclass

IWD_DIR = os.environ.get('EPILYKOS_IWD_DIR', '/data/iwd')
COUNTRY_FILE = os.environ.get('EPILYKOS_WIFI_COUNTRY_FILE', '/data/network/wifi-country')

HEX64 = re.compile(r'^[0-9A-Fa-f]{64}$')
COUNTRY = re.compile(r'^[A-Z]{2}$')


class WifiError(ValueError):
    """Unusable Wi-Fi settings. The message never contains the passphrase."""


@dataclass
class WifiNetwork:
    ssid: bytes
    passphrase: str = ''       # empty means an open network
    hidden: bool = False
    country: str = ''

    def validate(self):
        if not 1 <= len(self.ssid) <= 32:
            raise WifiError('SSID must be 1 to 32 bytes')
        p = self.passphrase
        if p and not HEX64.match(p):
            if not 8 <= len(p) <= 63:
                raise WifiError('Wi-Fi password must be 8 to 63 characters')
            if any(not (32 <= ord(c) <= 126) for c in p):
                raise WifiError('Wi-Fi password must be printable ASCII')
        if self.country and not COUNTRY.match(self.country):
            raise WifiError('country must be a two-letter code such as GB or NG')
        return self

    @property
    def display_ssid(self):
        return self.ssid.decode('utf-8', 'replace')


# -- iwd known-network files -------------------------------------------------

def iwd_file_name(ssid: bytes, open_network: bool) -> str:
    """iwd's own naming rule: plain SSID when it is only ASCII letters,
    digits, '-', '_' or space; otherwise '=' plus the SSID in hex."""
    ext = 'open' if open_network else 'psk'
    try:
        text = ssid.decode('ascii')
    except UnicodeDecodeError:
        text = None
    if text and all(c.isalnum() or c in '-_ ' for c in text):
        return f'{text}.{ext}'
    return f'={ssid.hex()}.{ext}'


def _settings_escape(value: str) -> str:
    # ell's settings parser unescapes \\, \s (leading space), \t, \n, \r.
    out = value.replace('\\', '\\\\').replace('\t', '\\t').replace('\n', '\\n').replace('\r', '\\r')
    if out.startswith(' '):
        out = '\\s' + out[1:]
    return out


def iwd_file_content(net: WifiNetwork) -> str:
    lines = []
    if net.passphrase:
        lines.append('[Security]')
        if HEX64.match(net.passphrase):
            lines.append('PreSharedKey=' + net.passphrase.lower())
        else:
            lines.append('Passphrase=' + _settings_escape(net.passphrase))
        lines.append('')
    lines.append('[Settings]')
    lines.append('AutoConnect=true')
    if net.hidden:
        lines.append('Hidden=true')
    return '\n'.join(lines) + '\n'


def _write_file(iwd_dir: str, name: str, text: str) -> str:
    os.makedirs(iwd_dir, mode=0o700, exist_ok=True)
    path = os.path.join(iwd_dir, name)
    fd, tmp = tempfile.mkstemp(dir=iwd_dir, prefix='.epilykos-')
    try:
        with os.fdopen(fd, 'w') as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o600)
        os.rename(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise
    return path


def write_iwd_network(net: WifiNetwork, iwd_dir: str = None) -> str:
    """Write (or replace) the known-network file for NET. Returns its path."""
    net.validate()
    iwd_dir = iwd_dir or IWD_DIR
    # A network can only be one type; drop a stale file of the other one.
    other = iwd_file_name(net.ssid, open_network=bool(net.passphrase))
    try:
        os.unlink(os.path.join(iwd_dir, other))
    except FileNotFoundError:
        pass
    return _write_file(iwd_dir, iwd_file_name(net.ssid, open_network=not net.passphrase), iwd_file_content(net))


def remove_iwd_network(ssid: bytes, iwd_dir: str = None):
    iwd_dir = iwd_dir or IWD_DIR
    for open_network in (False, True):
        try:
            os.unlink(os.path.join(iwd_dir, iwd_file_name(ssid, open_network)))
        except FileNotFoundError:
            pass


def saved_network(ssid: bytes, iwd_dir: str = None) -> dict:
    """SSID's known-network files as they are now ({name: text}; empty when
    it is not known), for restore_network to put back."""
    iwd_dir = iwd_dir or IWD_DIR
    saved = {}
    for open_network in (False, True):
        name = iwd_file_name(ssid, open_network)
        try:
            with open(os.path.join(iwd_dir, name)) as f:
                saved[name] = f.read()
        except FileNotFoundError:
            pass
    return saved


def restore_network(ssid: bytes, saved: dict, iwd_dir: str = None):
    """Put SSID back the way saved_network found it: a failed attempt with
    a mistyped password must not lose the settings that worked."""
    iwd_dir = iwd_dir or IWD_DIR
    remove_iwd_network(ssid, iwd_dir)
    for name, text in saved.items():
        _write_file(iwd_dir, name, text)


def configured_networks(iwd_dir: str = None):
    iwd_dir = iwd_dir or IWD_DIR
    try:
        return sorted(n for n in os.listdir(iwd_dir)
                      if n.endswith(('.psk', '.open')) and not n.startswith('.'))
    except FileNotFoundError:
        return []


def save_country(country: str, path: str = None):
    country = country.upper()
    if not COUNTRY.match(country):
        raise WifiError('country must be a two-letter code')
    path = path or COUNTRY_FILE
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        f.write(country + '\n')


def load_country(path: str = None) -> str:
    try:
        with open(path or COUNTRY_FILE) as f:
            value = f.read().strip().upper()
    except FileNotFoundError:
        return ''
    return value if COUNTRY.match(value) else ''


# -- Provisioning files on the BOOT partition ------------------------------

def decode_boot_file(data: bytes) -> str:
    """The text of a file someone saved on BOOT from their computer: UTF-8
    with or without a byte-order mark, or UTF-16 (what Windows Notepad
    writes for "Unicode"), read leniently so one odd byte doesn't lose the
    whole file."""
    if data.startswith((b'\xff\xfe', b'\xfe\xff')):
        return data.decode('utf-16', 'replace')
    if data.startswith(b'\xef\xbb\xbf'):
        data = data[3:]
    # A partly written file can end in zero bytes.
    return data.rstrip(b'\0').decode('utf-8', 'surrogateescape')


def is_blank(data: bytes) -> bool:
    """True for an empty file, or one that is only zero bytes: what a FAT
    card often holds when it was pulled out before the computer finished
    writing the file."""
    return not data.strip(b'\0 \t\r\n')


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in '"\'':
        return value[1:-1]
    return value


def _truthy(value: str) -> bool:
    return value.strip().lower() in ('1', 'true', 'yes', 'on')


def parse_wifi_txt(text: str) -> WifiNetwork:
    """EpilykosOS's own format (wifi.txt): key=value lines.

        ssid=My network
        password=my secret password
        country=GB
        hidden=no
    """
    values = {}
    for raw in text.splitlines():
        line = raw.strip().lstrip('\ufeff')
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        values[key.strip().lower()] = _unquote(value)
    if 'ssid' not in values:
        raise WifiError('wifi.txt has no ssid= line')
    ssid = values['ssid']
    if not ssid:
        raise WifiError('ssid= is empty in wifi.txt')
    password = values.get('password', values.get('psk', values.get('passphrase', '')))
    return WifiNetwork(ssid=ssid.encode('utf-8'), passphrase=password,
                       hidden=_truthy(values.get('hidden', '')),
                       country=values.get('country', '').upper()).validate()


def parse_wpa_supplicant(text: str) -> WifiNetwork:
    """A wpa_supplicant.conf with a network={} block: the classic Raspberry
    Pi headless file, and what Raspberry Pi Imager embeds in firstrun.sh."""
    country = ''
    m = re.search(r'^\s*country=([A-Za-z]{2})\s*$', text, re.M)
    if m:
        country = m.group(1).upper()
    block = re.search(r'network\s*=\s*\{(.*?)^\s*\}', text, re.S | re.M)
    if not block:
        raise WifiError('no network={} block')
    ssid = None
    passphrase = ''
    hidden = False
    open_network = False
    for raw in block.group(1).splitlines():
        line = raw.strip()
        if line.startswith('ssid='):
            v = line[5:]
            if v.startswith('"') and v.rfind('"') > 0:
                ssid = v[1:v.rfind('"')].encode('utf-8')
            else:
                v = v[4:] if v.startswith('hex:') else v
                try:
                    ssid = bytes.fromhex(v)
                except ValueError:
                    raise WifiError('unreadable ssid in network block') from None
        elif line.startswith('psk='):
            v = line[4:]
            if v.startswith('"') and v.rfind('"') > 0:
                # A quoted passphrase runs to the last '"' on the line.
                passphrase = v[1:v.rfind('"')]
            else:
                passphrase = v
        elif line.replace(' ', '') == 'key_mgmt=NONE':
            open_network = True
        elif line.replace(' ', '') == 'scan_ssid=1':
            hidden = True
    if not ssid:
        raise WifiError('network block has no ssid')
    if open_network:
        passphrase = ''
    return WifiNetwork(ssid=ssid, passphrase=passphrase, hidden=hidden, country=country).validate()


def parse_firstrun(text: str):
    """Raspberry Pi Imager's firstrun.sh ("systemd" customisation). The
    Wi-Fi settings are in its wpa_supplicant.conf here-document. Returns
    None when the script carries no Wi-Fi settings. Never executed."""
    m = re.search(r"cat\s*>\s*/etc/wpa_supplicant/wpa_supplicant\.conf\s*<<\s*'?([A-Za-z0-9_]+)'?\s*\n(.*?)\n\1\s*$",
                  text, re.S | re.M)
    if not m:
        return None
    return parse_wpa_supplicant(m.group(2))


_YAML_ESCAPES = {'0': '\0', 'a': '\a', 'b': '\b', 't': '\t', '\t': '\t', 'n': '\n',
                 'v': '\v', 'f': '\f', 'r': '\r', 'e': '\x1b', ' ': ' ', '"': '"',
                 '/': '/', '\\': '\\', 'N': '\u0085', '_': '\u00a0'}


def _yaml_scalar(value: str) -> bytes:
    """A YAML scalar (double-quoted, single-quoted or plain) as bytes.

    Escapes are code points, as YAML defines them. Raspberry Pi Imager
    writes UTF-8 SSIDs literally and uses one \\xHH per byte only for an
    SSID that is not UTF-8; that case is recognised and returned as the
    raw bytes."""
    value = value.strip()
    if value.startswith('"') and value.endswith('"') and len(value) >= 2:
        s = value[1:-1]
        out = []
        high_escape = False
        i = 0
        while i < len(s):
            c = s[i]
            if c != '\\':
                out.append(c)
                i += 1
                continue
            e = s[i + 1] if i + 1 < len(s) else ''
            if e in _YAML_ESCAPES:
                out.append(_YAML_ESCAPES[e])
                i += 2
            elif e in 'xuU':
                n = {'x': 2, 'u': 4, 'U': 8}[e]
                try:
                    code = int(s[i + 2:i + 2 + n], 16)
                except ValueError:
                    raise WifiError('unreadable escape in network-config') from None
                high_escape |= e == 'x' and code >= 0x80
                out.append(chr(code))
                i += 2 + n
            else:
                raise WifiError('unsupported escape in network-config')
        text = ''.join(out)
        literal_ascii = all(ord(ch) < 0x80 for ch in s)
        if high_escape and literal_ascii and all(ord(ch) < 0x100 for ch in text):
            raw = text.encode('latin-1')
            try:
                raw.decode('utf-8')
            except UnicodeDecodeError:
                return raw
        return text.encode('utf-8')
    if value.startswith("'") and value.endswith("'") and len(value) >= 2:
        return value[1:-1].replace("''", "'").encode('utf-8')
    return value.split(' #', 1)[0].strip().encode('utf-8')


def parse_network_config(text: str):
    """cloud-init network-config (netplan v2) as Raspberry Pi Imager writes
    it: the first access point under wifis. Returns None without one."""
    lines = text.splitlines()
    country = ''
    m = re.search(r'^\s*regulatory-domain:\s*(.+)$', text, re.M)
    if m:
        country = _yaml_scalar(m.group(1)).decode('ascii', 'replace').upper()
    for i, line in enumerate(lines):
        if not re.match(r'^\s*access-points:\s*$', line):
            continue
        base = len(line) - len(line.lstrip())
        ssid = None
        ap_indent = None
        passphrase = ''
        hidden = False
        for nxt in lines[i + 1:]:
            if not nxt.strip() or nxt.lstrip().startswith('#'):
                continue
            indent = len(nxt) - len(nxt.lstrip())
            if indent <= base:
                break
            body = nxt.strip()
            if ssid is None:
                key = re.match(r'^("(?:[^"\\]|\\.)*"|\'(?:[^\']|\'\')*\'|[^:#]+):\s*$', body)
                if not key:
                    raise WifiError('unreadable access point in network-config')
                ssid = _yaml_scalar(key.group(1))
                ap_indent = indent
                continue
            if indent <= ap_indent:
                break  # a second access point; the first one is used
            if body.startswith('password:'):
                passphrase = _yaml_scalar(body[len('password:'):]).decode('utf-8')
            elif re.match(r'^hidden:\s*true\s*$', body):
                hidden = True
        if ssid:
            return WifiNetwork(ssid=ssid, passphrase=passphrase, hidden=hidden,
                               country=country).validate()
    return None


def parse_preseed_toml(text: str):
    """rpi-preseed.toml, Raspberry Pi Imager's newest customisation format.
    Returns None when it has no [wlan] table."""
    import tomllib
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        raise WifiError('rpi-preseed.toml is not valid TOML') from None
    wlan = data.get('wlan')
    if not wlan:
        return None
    if wlan.get('ssid_hex'):
        try:
            ssid = bytes.fromhex(str(wlan['ssid_hex']))
        except ValueError:
            raise WifiError('unreadable ssid_hex in rpi-preseed.toml') from None
    else:
        ssid = str(wlan.get('ssid', '')).encode('utf-8')
    return WifiNetwork(ssid=ssid, passphrase=str(wlan.get('password', '')),
                       hidden=bool(wlan.get('hidden', False)),
                       country=str(wlan.get('country', '')).upper()).validate()


# File on BOOT -> parser. Order is the order they are applied; a later file
# for the same SSID wins.
BOOT_SOURCES = [
    ('wpa_supplicant.conf', parse_wpa_supplicant),
    ('firstrun.sh', parse_firstrun),
    ('network-config', parse_network_config),
    ('rpi-preseed.toml', parse_preseed_toml),
    ('wifi.txt', parse_wifi_txt),
    ('wifi.txt.txt', parse_wifi_txt),  # Windows hides the .txt it adds
]
