#!/usr/bin/env python3
"""The network status Settings -> Network shows (epilykos_netinfo): default
gateway, Wi-Fi link and channel, without touching the test host's network."""
import sys
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'os/board/epilykos/raspberrypi3/rootfs-overlay/usr/lib/epilykos/python'))
import epilykos_netinfo as net  # noqa: E402

ROUTE = ('Iface\tDestination\tGateway \tFlags\tRefCnt\tUse\tMetric\tMask\t\tMTU\tWindow\tIRTT\n'
         'wlan0\t0000A8C0\t00000000\t0001\t0\t0\t600\t00FFFFFF\t0\t0\t0\n'
         'wlan0\t00000000\t0100A8C0\t0003\t0\t0\t600\t00000000\t0\t0\t0\n')
# `iw dev wlan0 link` on the Pi 3B, 2026-10-21 (joined to ASC on channel 1).
LINK = """Connected to ac:84:c6:fe:00:ff (on wlan0)
\tSSID: ASC
\tfreq: 2412.0
\tRX: 1234 bytes (10 packets)
\tsignal: -78 dBm
\trx bitrate: 58.5 MBit/s MCS 6
\ttx bitrate: 14.4 MBit/s MCS 1
"""

with tempfile.TemporaryDirectory() as d:
    d = Path(d)
    (d / 'route').write_text(ROUTE)
    assert net.default_gateway(str(d / 'route')) == ('192.168.0.1', 'wlan0')
    (d / 'route2').write_text(ROUTE.splitlines()[0] + '\n')
    assert net.default_gateway(str(d / 'route2')) == (None, None), 'no default route'
    assert net.default_gateway(str(d / 'missing')) == (None, None)

    assert [net.channel(f) for f in (2412, 2437, 2462, 2472, 2484, 5180, 5745, 5955, 1234)] == [1, 6, 11, 13, 14, 36, 149, 1, None]
    link = net.parse_iw_link(LINK)
    assert link == {'connected': True, 'ssid': 'ASC', 'frequency': 2412, 'channel': 1, 'signal_dbm': -78, 'tx_bitrate': '14.4 Mbit/s'}, link
    assert net.parse_iw_link('Not connected.\n') == {'connected': False}

    # wifi_link runs iw only when the interface exists.
    calls = d / 'calls'
    (d / 'iw').write_text(f'#!/bin/sh\necho "$@" >> {calls}\ncat <<"X"\n{LINK}X\n')
    (d / 'iw').chmod(0o755)
    net.IW, net.SYS_NET = str(d / 'iw'), str(d / 'sys')
    assert net.wifi_link() is None and not calls.exists(), 'no wlan0: nothing to ask'
    (d / 'sys/wlan0').mkdir(parents=True)
    assert net.wifi_link() == dict(link, interface='wlan0')
    assert calls.read_text().split() == ['dev', 'wlan0', 'link']

print('PASS network status: default gateway, Wi-Fi link (network, signal, channel), no Wi-Fi interface')
