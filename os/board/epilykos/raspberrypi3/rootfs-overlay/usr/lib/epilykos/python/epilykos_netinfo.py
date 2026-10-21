"""The box's network as Settings -> Network shows it: the default gateway,
the Wi-Fi link (network, signal, channel) and the Wi-Fi country. Read-only;
the app lists the addresses itself (it shares the host's network)."""
import os
import socket
import struct
import subprocess

IW = os.environ.get('EPILYKOS_IW', 'iw')
PROC_ROUTE = os.environ.get('EPILYKOS_PROC_ROUTE', '/proc/net/route')
SYS_NET = os.environ.get('EPILYKOS_SYS_NET', '/sys/class/net')


def default_gateway(path=None):
    """(gateway IPv4, interface) of the default route, or (None, None)."""
    try:
        with open(path or PROC_ROUTE) as f:
            next(f, None)  # header
            for line in f:
                cols = line.split()
                if len(cols) >= 4 and cols[1] == '00000000' and int(cols[3], 16) & 0x2:
                    return socket.inet_ntoa(struct.pack('<L', int(cols[2], 16))), cols[0]
    except (OSError, ValueError):
        pass
    return None, None


def channel(freq):
    """802.11 channel of a centre frequency in MHz (2.4, 5 and 6 GHz)."""
    if freq == 2484:
        return 14
    if 2412 <= freq <= 2472:
        return (freq - 2407) // 5
    if 5160 <= freq <= 5885:
        return (freq - 5000) // 5
    if 5955 <= freq <= 7115:
        return (freq - 5950) // 5
    return None


def parse_iw_link(text):
    """The useful parts of `iw dev <if> link`."""
    if not text or text.startswith('Not connected'):
        return {'connected': False}
    out = {'connected': True}
    for line in text.splitlines():
        key, _, value = line.strip().partition(':')
        value = value.strip()
        if key == 'SSID':
            out['ssid'] = value
        elif key == 'freq':
            try:
                out['frequency'] = int(float(value))
                out['channel'] = channel(out['frequency'])
            except ValueError:
                pass
        elif key == 'signal':
            try:
                out['signal_dbm'] = int(value.split()[0])
            except (ValueError, IndexError):
                pass
        elif key == 'tx bitrate':
            out['tx_bitrate'] = value.split(' MBit/s')[0] + ' Mbit/s' if 'MBit/s' in value else value
    return out


def wifi_link(ifname='wlan0'):
    if not os.path.exists(os.path.join(SYS_NET, ifname)):
        return None  # no Wi-Fi interface (e.g. its driver failed)
    try:
        r = subprocess.run([IW, 'dev', ifname, 'link'], capture_output=True, text=True, timeout=3)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    return dict(parse_iw_link(r.stdout), interface=ifname)


def status(zone=None):
    import epilykos_wifi as wifi
    gateway, gateway_if = default_gateway()
    country, source = wifi.effective_country(zone) if zone else (wifi.load_country(), 'Wi-Fi settings')
    return {'gateway': gateway, 'gateway_interface': gateway_if, 'wifi': wifi_link(),
            'wifi_country': country or None, 'wifi_country_source': source or None}
