#!/usr/bin/env python3
"""Self-tests for the OS image's Wi-Fi provisioning (epilykos_wifi and
epilykos-wifi-import): every file format a user can drop on BOOT, the iwd
known-network files they turn into, and that passwords never reach the log.
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True  # keep __pycache__ out of the rootfs overlay
ROOT = Path(__file__).resolve().parent.parent
OVERLAY = ROOT / 'os/board/epilykos/raspberrypi3/rootfs-overlay'
sys.path.insert(0, str(OVERLAY / 'usr/lib/epilykos/python'))
import epilykos_wifi as wifi  # noqa: E402

SECRET = 'correct horse battery'


def ok(name):
    print(f'ok - {name}')


def expect_error(name, fn, text):
    try:
        fn(text)
    except wifi.WifiError as e:
        assert SECRET not in str(e), f'{name}: error message leaks the password'
        ok(name)
        return
    raise AssertionError(f'{name}: expected WifiError')


# Raspberry Pi Imager's "systemd" customisation, as customization_generator.cpp writes it.
FIRSTRUN = f"""#!/bin/bash

set +e

CURRENT_HOSTNAME=`cat /etc/hostname | tr -d " \\t\\n\\r"`
if [ -f /usr/lib/raspberrypi-sys-mods/imager_custom ]; then
   /usr/lib/raspberrypi-sys-mods/imager_custom set_hostname epilykos
else
   echo epilykos >/etc/hostname
fi
if [ -f /usr/lib/raspberrypi-sys-mods/imager_custom ] && true; then
   /usr/lib/raspberrypi-sys-mods/imager_custom set_wlan  -p 'Home Net' '{SECRET}' 'NG'
else
cat >/etc/wpa_supplicant/wpa_supplicant.conf <<'WPAEOF'
country=NG
ctrl_interface=DIR=/var/run/wpa_supplicant GROUP=netdev
ap_scan=1

update_config=1
network={{
	ssid="Home Net"

	key_mgmt=WPA-PSK SAE
	psk="{SECRET}"
	ieee80211w=1
}}
WPAEOF
   chmod 600 /etc/wpa_supplicant/wpa_supplicant.conf
   rfkill unblock wifi
   for filename in /var/lib/systemd/rfkill/*:wlan ; do
       echo 0 > $filename
   done
fi
rm -f /boot/firstrun.sh
sed -i 's| systemd.run.*||g' /boot/cmdline.txt
exit 0
"""

NETWORK_CONFIG = f"""network:
  version: 2
  ethernets:
    eth0:
      dhcp4: true
      dhcp6: true
      optional: true
  wifis:
    wlan0:
      dhcp4: true
      regulatory-domain: "GB"
      access-points:
        "Caf\\xe9 \\"5G\\"":
          hidden: true
          password: "{SECRET}"
      optional: true
"""

PRESEED = f"""[wlan]
ssid = "Home Net"
password = "{SECRET}"
hidden = false
country = "ng"
"""

# -- parsers ------------------------------------------------------------------
n = wifi.parse_firstrun(FIRSTRUN)
assert (n.ssid, n.passphrase, n.country, n.hidden) == (b'Home Net', SECRET, 'NG', False), n
ok('Imager firstrun.sh: ssid, password, country')

assert wifi.parse_firstrun('#!/bin/bash\necho epilykos >/etc/hostname\n') is None
ok('Imager firstrun.sh without Wi-Fi gives no network')

hexed = FIRSTRUN.replace('ssid="Home Net"', 'ssid=hex:caf3').replace(
    f'psk="{SECRET}"', 'psk=' + 'ab' * 32).replace('\tieee80211w=1\n', '\tieee80211w=1\n\tscan_ssid=1\n')
n = wifi.parse_firstrun(hexed)
assert (n.ssid, n.passphrase, n.hidden) == (b'\xca\xf3', 'ab' * 32, True), n
ok('Imager firstrun.sh: hex SSID, raw 64-hex PSK, hidden')

n = wifi.parse_wpa_supplicant('network={\n ssid="Open Cafe"\n key_mgmt=NONE\n}\n')
assert (n.ssid, n.passphrase) == (b'Open Cafe', ''), n
ok('wpa_supplicant.conf: open network')

n = wifi.parse_network_config(NETWORK_CONFIG)
assert (n.ssid, n.passphrase, n.hidden, n.country) == (b'Caf\xe9 "5G"', SECRET, True, 'GB'), n
ok('Imager network-config: escaped SSID, hidden, regulatory domain')

n = wifi.parse_network_config(NETWORK_CONFIG.replace('"Caf\\xe9 \\"5G\\""', '"Café\\x85"'))
assert n.ssid == 'Café\x85'.encode('utf-8'), n.ssid
n = wifi.parse_network_config(NETWORK_CONFIG.replace('"Caf\\xe9 \\"5G\\""', 'Plain SSID'))
assert n.ssid == b'Plain SSID', n.ssid
ok('network-config: UTF-8 SSIDs (literal or code-point escapes) and plain keys')

assert wifi.parse_network_config('network:\n  version: 2\n  ethernets:\n    eth0:\n      dhcp4: true\n') is None
ok('network-config without wifis gives no network')

n = wifi.parse_preseed_toml(PRESEED)
assert (n.ssid, n.passphrase, n.country) == (b'Home Net', SECRET, 'NG'), n
ok('Imager rpi-preseed.toml')

n = wifi.parse_wifi_txt(f'# EpilykosOS Wi-Fi\nSSID = Home Net\npassword={SECRET}\ncountry=ng\nhidden=yes\n')
assert (n.ssid, n.passphrase, n.country, n.hidden) == (b'Home Net', SECRET, 'NG', True), n
ok('wifi.txt: keys are case-insensitive, hidden=yes')

n = wifi.parse_wifi_txt('ssid="  spaced  "\npassword="a=b=c=d#e!"\n')
assert (n.ssid, n.passphrase) == (b'  spaced  ', 'a=b=c=d#e!'), n
ok('wifi.txt: quoted values keep spaces, = and # in passwords')

expect_error('wifi.txt without ssid', wifi.parse_wifi_txt, f'password={SECRET}\n')
expect_error('password too short', wifi.parse_wifi_txt, 'ssid=x\npassword=short\n')
expect_error('bad country', wifi.parse_wifi_txt, f'ssid=x\npassword={SECRET}\ncountry=Nigeria\n')

# -- iwd files ------------------------------------------------------------------
assert wifi.iwd_file_name(b'Home Net', False) == 'Home Net.psk'
assert wifi.iwd_file_name(b'my-wifi_2', True) == 'my-wifi_2.open'
assert wifi.iwd_file_name(b'Caf\xc3\xa9', False) == '=436166c3a9.psk'
assert wifi.iwd_file_name(b'a.b', False) == '=612e62.psk'
ok('iwd file names follow iwd\'s plain/hex rule')

with tempfile.TemporaryDirectory() as d:
    path = wifi.write_iwd_network(wifi.WifiNetwork(b'Home Net', ' back\\slash pass'), d)
    text = Path(path).read_text()
    assert text == '[Security]\nPassphrase=\\sback\\\\slash pass\n\n[Settings]\nAutoConnect=true\n', text
    assert os.stat(path).st_mode & 0o777 == 0o600
    wifi.write_iwd_network(wifi.WifiNetwork(b'Home Net', '', hidden=True), d)
    assert wifi.configured_networks(d) == ['Home Net.open'], wifi.configured_networks(d)
    assert 'Hidden=true' in Path(d, 'Home Net.open').read_text()
    wifi.write_iwd_network(wifi.WifiNetwork(b'Home Net', 'ab' * 32), d)
    assert Path(d, 'Home Net.psk').read_text().startswith('[Security]\nPreSharedKey=' + 'ab' * 32)
    assert wifi.configured_networks(d) == ['Home Net.psk']
    wifi.remove_iwd_network(b'Home Net', d)
    assert wifi.configured_networks(d) == []
ok('iwd files: escaping, 0600, one file per SSID, PSK, remove')

# -- epilykos-wifi-import end to end --------------------------------------------
with tempfile.TemporaryDirectory() as d:
    boot, iwd, country = Path(d, 'boot'), Path(d, 'iwd'), Path(d, 'data/network/wifi-country')
    boot.mkdir()
    (boot / 'firstrun.sh').write_text(FIRSTRUN)
    (boot / 'network-config').write_text(NETWORK_CONFIG)
    (boot / 'wifi.txt').write_text('ssid=Home Net\npassword=short\n')  # invalid: stays
    (boot / 'config.txt').write_text('kernel=u-boot.bin\n')
    env = dict(os.environ, EPILYKOS_BOOT_DIR=str(boot), EPILYKOS_IWD_DIR=str(iwd),
               EPILYKOS_WIFI_COUNTRY_FILE=str(country),
               PYTHONPATH=str(OVERLAY / 'usr/lib/epilykos/python'), PYTHONDONTWRITEBYTECODE='1')
    r = subprocess.run([sys.executable, str(OVERLAY / 'usr/bin/epilykos-wifi-import')],
                       env=env, capture_output=True, text=True)
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    assert SECRET not in out, 'import log leaks the password:\n' + out
    expected = sorted([wifi.iwd_file_name(b'Caf\xe9 "5G"', False), 'Home Net.psk'])
    assert sorted(os.listdir(iwd)) == expected, os.listdir(iwd)
    assert sorted(os.listdir(boot)) == ['config.txt', 'wifi.txt'], os.listdir(boot)
    assert country.read_text() == 'GB\n', country.read_text()  # network-config is applied after firstrun.sh
    assert 'wifi.txt: not imported' in out, out
ok('epilykos-wifi-import: imports Imager files, deletes them, keeps bad ones, never logs passwords')

# -- Raspberry Pi Imager OS list ---------------------------------------------------
with tempfile.TemporaryDirectory() as d:
    img, dl = Path(d, 'sdcard.img'), Path(d, 'sdcard.img.xz')
    img.write_bytes(b'\0' * 4096)
    dl.write_bytes(b'xz')
    r = subprocess.run([sys.executable, str(ROOT / 'tools/imager_os_list.py'), '--image', str(img),
                        '--download', str(dl), '--url', 'https://example.invalid/sdcard.img.xz',
                        '--release-date', '2026-10-15'], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    import json
    entry = json.loads(r.stdout)['os_list'][0]
    required = ['name', 'description', 'icon', 'url', 'extract_size', 'extract_sha256',
                'image_download_size', 'release_date', 'devices']  # Imager's os-list schema
    assert all(k in entry for k in required), entry
    assert entry['init_format'] == 'systemd' and entry['extract_size'] == 4096
    assert entry['extract_sha256'] == 'ad7facb2586fc6e966c004d7d1d16b024f5805ff7cb47c7a85dabd8b48892ca7'
    assert entry['icon'].endswith('/os/imager/icon.png') and (ROOT / 'os/imager/icon.png').is_file()
ok('Imager OS list: schema fields, systemd customisation, image checksum')

print('\nPASS Wi-Fi provisioning self-tests')
