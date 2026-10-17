#!/usr/bin/env python3
"""Integration test for epilykos-improv (Wi-Fi setup over Bluetooth LE).

Runs the real daemon code against fake BlueZ and iwd services on a private
D-Bus session bus, and plays the part of a phone: it registers the GATT
application and advertisements the way bluetoothd does, checks they fit in
a legacy advertisement, and drives the Improv RPCs (send Wi-Fi settings,
scan, device info, bad packets) over GattCharacteristic1.WriteValue.

Needs dbus-fast and dbus-run-session (re-executes itself under it).
"""
import asyncio
import importlib.machinery
import importlib.util
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.dont_write_bytecode = True  # keep __pycache__ out of the rootfs overlay
ROOT = Path(__file__).resolve().parent.parent
OVERLAY = ROOT / 'os/board/epilykos/raspberrypi3/rootfs-overlay'

if 'DBUS_SESSION_BUS_ADDRESS' not in os.environ or os.environ.get('EPILYKOS_TEST_BUS') != '1':
    if not shutil.which('dbus-run-session'):
        sys.exit('dbus-run-session is required')
    os.environ['EPILYKOS_TEST_BUS'] = '1'
    os.execvp('dbus-run-session', ['dbus-run-session', '--', sys.executable, __file__])

IWD_DIR = tempfile.mkdtemp(prefix='iwd-')
os.environ['EPILYKOS_IWD_DIR'] = IWD_DIR
WINDOW_FILE = os.path.join(tempfile.mkdtemp(prefix='run-'), 'wifi-setup-until')
os.environ['EPILYKOS_WIFI_SETUP_FILE'] = WINDOW_FILE
sys.path.insert(0, str(OVERLAY / 'usr/lib/epilykos/python'))
loader = importlib.machinery.SourceFileLoader('improv', str(OVERLAY / 'usr/bin/epilykos-improv'))
spec = importlib.util.spec_from_loader('improv', loader)
improv = importlib.util.module_from_spec(spec)
loader.exec_module(improv)

from dbus_fast import BusType, DBusError, Message, MessageType, Variant  # noqa: E402
from dbus_fast.aio import MessageBus  # noqa: E402
from dbus_fast.constants import PropertyAccess  # noqa: E402
from dbus_fast.service import ServiceInterface, dbus_property, method  # noqa: E402

SECRET = 'correct horse battery'


def ok(name):
    print(f'ok - {name}')


# -- packet codec ----------------------------------------------------------------
pkt = improv.encode_rpc(1, bytes([12]) + b'MyWirelessAP' + bytes([16]) + b'mysecurepassword')
assert pkt[:3] == bytes([0x01, 0x1E, 0x0C]), pkt  # the spec's own example
assert improv.decode_rpc(pkt) == (1, pkt[2:-1])
assert improv.decode_wifi_settings(pkt[2:-1]) == (b'MyWirelessAP', 'mysecurepassword')
for bad in (pkt[:-1] + bytes([(pkt[-1] + 1) & 0xFF]), pkt[:-2], b'\x01'):
    try:
        improv.decode_rpc(bad)
        raise AssertionError('accepted a bad packet')
    except improv.RpcError as e:
        assert e.code == improv.ERROR_INVALID_RPC
r = improv.encode_result(3, ['EpilykosOS', '1'])
assert r == bytes([3, 13, 10]) + b'EpilykosOS' + bytes([1]) + b'1' + bytes([improv.checksum(r[:-1])])
a = improv.RpcAssembler()
assert a.feed(pkt[:20], now=0) is None and a.feed(pkt[20:], now=1) == pkt
assert a.feed(pkt[:20], now=2) is None and a.feed(pkt[20:], offset=20, now=2) == pkt
assert a.feed(pkt[:20], now=3) is None and a.feed(pkt, now=10) == pkt  # stale partial dropped
ok('Improv packets: spec example, checksum, chunked and offset writes')


# -- fake BlueZ ---------------------------------------------------------------------
def adv_length(props):
    """Legacy advertising data size BlueZ would build from these properties."""
    n = 3  # flags
    uuids = props.get('ServiceUUIDs', Variant('as', [])).value
    if uuids:
        n += 2 + 16 * len(uuids)
    for uuid, data in props.get('ServiceData', Variant('a{sv}', {})).value.items():
        n += 2 + (2 if uuid.endswith('-0000-1000-8000-00805f9b34fb') else 16) + len(data.value)
    if 'LocalName' in props:
        n += 2 + len(props['LocalName'].value)
    return n


class FakeAdapter(ServiceInterface):
    def __init__(self):
        super().__init__('org.bluez.Adapter1')
        self.powered = False
        self.alias = 'BlueZ'

    @dbus_property()
    def Powered(self) -> 'b':
        return self.powered

    @Powered.setter
    def Powered(self, v: 'b'):
        self.powered = v

    @dbus_property()
    def Alias(self) -> 's':
        return self.alias

    @Alias.setter
    def Alias(self, v: 's'):
        self.alias = v


class FakeGattManager(ServiceInterface):
    def __init__(self, bus, app_owner):
        super().__init__('org.bluez.GattManager1')
        self.bus, self.app_owner, self.objects = bus, app_owner, None

    @method()
    async def RegisterApplication(self, app: 'o', options: 'a{sv}'):
        reply = await self.bus.call(Message(destination=self.app_owner(), path=app,
                                            interface='org.freedesktop.DBus.ObjectManager',
                                            member='GetManagedObjects'))
        assert reply.message_type == MessageType.METHOD_RETURN, reply.body
        self.objects = reply.body[0]


class FakeAdvertisingManager(ServiceInterface):
    def __init__(self, bus, app_owner):
        super().__init__('org.bluez.LEAdvertisingManager1')
        self.bus, self.app_owner = bus, app_owner
        self.active, self.registrations = {}, 0
        self.max_ads, self.refuse = 5, False  # refuse: slots stuck, as seen on a Pi 3B
        self.unregister_delays = []  # seconds, consumed one per UnregisterAdvertisement

    @method()
    async def RegisterAdvertisement(self, adv: 'o', options: 'a{sv}'):
        # bluetoothd: a path registered twice is refused, and so is any
        # registration once every advertising slot is in use.
        if adv in self.active:
            raise DBusError('org.bluez.Error.AlreadyExists', 'Already Exists')
        if self.refuse or len(self.active) >= self.max_ads:
            raise DBusError('org.bluez.Error.Failed', 'Maximum advertisements reached')
        reply = await self.bus.call(Message(destination=self.app_owner(), path=adv,
                                            interface='org.freedesktop.DBus.Properties', member='GetAll',
                                            signature='s', body=['org.bluez.LEAdvertisement1']))
        props = reply.body[0]
        if adv_length(props) > 31:
            raise DBusError('org.bluez.Error.InvalidLength', 'Advertising data too long')
        self.active[adv] = props
        self.registrations += 1

    @method()
    async def UnregisterAdvertisement(self, adv: 'o'):
        if self.unregister_delays:
            await asyncio.sleep(self.unregister_delays.pop(0))
        if adv not in self.active:
            raise DBusError('org.bluez.Error.DoesNotExist', 'Does Not Exist')
        del self.active[adv]


# -- fake iwd -----------------------------------------------------------------------
STATION = '/net/connman/iwd/0/4'


class FakeDevice(ServiceInterface):
    def __init__(self):
        super().__init__('net.connman.iwd.Device')

    @dbus_property(access=PropertyAccess.READ)
    def Name(self) -> 's':
        return 'wlantest'


class FakeStation(ServiceInterface):
    def __init__(self):
        super().__init__('net.connman.iwd.Station')
        self.scans = 0

    @dbus_property(access=PropertyAccess.READ)
    def Scanning(self) -> 'b':
        return False

    @method()
    def Scan(self):
        self.scans += 1

    @method()
    def GetOrderedNetworks(self) -> 'a(on)':
        return [[STATION + '/home_psk', -5000], [STATION + '/bad_psk', -7200], [STATION + '/cafe_open', -8100]]

    @method()
    def ConnectHiddenNetwork(self, name: 's'):
        raise DBusError('net.connman.iwd.NotFound', 'no hidden network')


class FakeNetwork(ServiceInterface):
    connected = None

    def __init__(self, name, kind, works=True):
        super().__init__('net.connman.iwd.Network')
        self.ssid, self.kind, self.works = name, kind, works

    @dbus_property(access=PropertyAccess.READ)
    def Name(self) -> 's':
        return self.ssid

    @dbus_property(access=PropertyAccess.READ)
    def Type(self) -> 's':
        return self.kind

    @method()
    def Connect(self):
        known = os.path.join(IWD_DIR, improv.wifi.iwd_file_name(self.ssid.encode(), self.kind == 'open'))
        if not self.works or not os.path.exists(known):
            raise DBusError('net.connman.iwd.Failed', 'Operation failed')
        FakeNetwork.connected = self.ssid


# -- the test -----------------------------------------------------------------------
async def wait_for(predicate, what, timeout=10):
    for _ in range(int(timeout / 0.05)):
        if await predicate():
            return
        await asyncio.sleep(0.05)
    raise AssertionError(f'timed out waiting for {what}')


async def main():
    app_bus = await MessageBus(bus_type=BusType.SESSION).connect()
    bluez_bus = await MessageBus(bus_type=BusType.SESSION).connect()
    iwd_bus = await MessageBus(bus_type=BusType.SESSION).connect()

    adapter = FakeAdapter()
    gatt = FakeGattManager(bluez_bus, lambda: app_bus.unique_name)
    advm = FakeAdvertisingManager(bluez_bus, lambda: app_bus.unique_name)
    for iface in (adapter, gatt, advm):
        bluez_bus.export('/org/bluez/hci0', iface)
    await bluez_bus.request_name('org.bluez')

    station = FakeStation()
    iwd_bus.export(STATION, FakeDevice())
    iwd_bus.export(STATION, station)
    iwd_bus.export(STATION + '/home_psk', FakeNetwork('Home Net', 'psk'))
    iwd_bus.export(STATION + '/bad_psk', FakeNetwork('Bad Net', 'psk', works=False))
    iwd_bus.export(STATION + '/cafe_open', FakeNetwork('Cafe', 'open'))
    await iwd_bus.request_name('net.connman.iwd')

    improv.ipv4_address = lambda ifname: '192.0.2.10' if FakeNetwork.connected and ifname == 'wlantest' else None
    improv.online_address = lambda: '192.0.2.10' if FakeNetwork.connected else None

    service = improv.Improv(app_bus)
    service.export()
    found = await service.find_adapter()
    assert found == '/org/bluez/hci0', found
    await service.register(found)
    assert adapter.powered and adapter.alias == 'Epilykos'
    uuids = sorted(v['org.bluez.GattCharacteristic1']['UUID'].value
                   for v in gatt.objects.values() if 'org.bluez.GattCharacteristic1' in v)
    assert uuids == sorted([improv.STATE_UUID, improv.ERROR_UUID, improv.RPC_COMMAND_UUID,
                            improv.RPC_RESULT_UUID, improv.CAPABILITIES_UUID]), uuids
    assert any(v.get('org.bluez.GattService1', {}).get('UUID', Variant('s', '')).value == improv.SERVICE_UUID
               for v in gatt.objects.values())
    ok('GATT application registers with the Improv service and its five characteristics')

    assert service.should_advertise(), 'offline box must advertise'
    await service.advertise(True)
    assert set(advm.active) == {improv.ADV_PATH, improv.NAME_ADV_PATH}, advm.active
    data = advm.active[improv.ADV_PATH]['ServiceData'].value[improv.SERVICE_DATA_UUID].value
    assert data[0] == improv.STATE_AUTHORIZED and len(data) == 6, data
    assert adv_length(advm.active[improv.ADV_PATH]) == 31
    ok('advertisements fit in 31 bytes: Improv UUID + service data, and the name separately')

    svc = improv.APP_PATH + '/service0'

    async def gatt_call(char, member, signature='', body=()):
        reply = await bluez_bus.call(Message(destination=app_bus.unique_name, path=f'{svc}/{char}',
                                             interface='org.bluez.GattCharacteristic1', member=member,
                                             signature=signature, body=list(body)))
        assert reply.message_type == MessageType.METHOD_RETURN, (member, reply.body)
        return reply.body

    async def read(char):
        return (await gatt_call(char, 'ReadValue', 'a{sv}', [{}]))[0]

    async def write(packet, chunk=20):
        for i in range(0, len(packet), chunk):
            await gatt_call('char2', 'WriteValue', 'aya{sv}', [packet[i:i + chunk], {}])

    def wifi_packet(ssid, password):
        s, p = ssid.encode(), password.encode()
        return improv.encode_rpc(1, bytes([len(s)]) + s + bytes([len(p)]) + p)

    for char in ('char0', 'char1', 'char3'):
        await gatt_call(char, 'StartNotify')
    caps = (await read('char4'))[0]
    assert caps & improv.CAP_SCAN and caps & improv.CAP_DEVICE_INFO

    # A wrong password: back to authorized, "unable to connect", no network left behind.
    regs = advm.registrations
    await write(wifi_packet('Bad Net', SECRET))
    await wait_for(lambda: _eq(read('char1'), [improv.ERROR_UNABLE_TO_CONNECT]), 'unable-to-connect error')
    assert (await read('char0')) == bytes([improv.STATE_AUTHORIZED])
    assert improv.wifi.configured_networks(IWD_DIR) == []
    ok('failed join: error 0x03, state back to authorized, credentials removed')

    # The real thing, sent in 20-byte chunks like a small-MTU client.
    await write(wifi_packet('Home Net', SECRET))
    await wait_for(lambda: _eq(read('char0'), [improv.STATE_PROVISIONED]), 'provisioned state')
    result = await read('char3')
    assert result[0] == 1 and result[3:3 + result[2]] == b'http://192.0.2.10:3000/', result
    assert improv.wifi.configured_networks(IWD_DIR) == ['Home Net.psk']
    assert advm.registrations > regs, 'state change must re-advertise'
    assert advm.active[improv.ADV_PATH]['ServiceData'].value[improv.SERVICE_DATA_UUID].value[0] == improv.STATE_PROVISIONED
    ok('join: provisioned, redirect URL to the dashboard, known network on DATA, re-advertised')

    service.started -= improv.SETUP_WINDOW_S + 1
    assert not service.should_advertise(), 'online with Wi-Fi configured must stop advertising'
    await service.advertise(False)
    assert advm.active == {}
    ok('stops advertising once online with Wi-Fi configured')

    # "Change Wi-Fi" in the dashboard opens setup while online (D-PROV-001).
    with open(WINDOW_FILE, 'w') as f:
        f.write(str(time.time() + 900))
    assert service.should_advertise(), 'dashboard window must open setup'
    with open(WINDOW_FILE, 'w') as f:
        f.write(str(time.time() - 1))
    assert not service.should_advertise(), 'an expired dashboard window must not'
    os.unlink(WINDOW_FILE)
    ok('dashboard "Change Wi-Fi" opens setup for its window only')

    # Network lost with Wi-Fi configured: setup opens only after the grace period.
    FakeNetwork.connected = False
    assert not service.should_advertise(), 'must wait out a short network loss'
    service.offline_since -= improv.OFFLINE_GRACE_S + 1
    assert service.should_advertise(), 'must reopen after 2 minutes offline'
    FakeNetwork.connected = True
    assert not service.should_advertise()
    ok('network loss reopens setup after the grace period, not before')

    await write(improv.encode_rpc(improv.CMD_SCAN, b''))
    await wait_for(lambda: _startswith(read('char3'), bytes([improv.CMD_SCAN])), 'scan result')
    result = await read('char3')
    strings, i = [], 2
    while i < len(result) - 1:
        strings.append(result[i + 1:i + 1 + result[i]].decode())
        i += 1 + result[i]
    assert strings == ['Home Net', '-50', 'WPA2', 'Bad Net', '-72', 'WPA2', 'Cafe', '-81', 'NO'], strings
    ok('scan: SSID, RSSI and security of nearby networks')

    await write(improv.encode_rpc(improv.CMD_DEVICE_INFO, b''))
    result = await read('char3')
    assert result[0] == improv.CMD_DEVICE_INFO and b'EpilykosOS' in result, result
    ok('device info')

    await write(improv.encode_rpc(0x7E, b''))
    assert (await read('char1')) == bytes([improv.ERROR_UNKNOWN_RPC])
    bad = bytearray(wifi_packet('Home Net', SECRET))
    bad[-1] ^= 0xFF
    await write(bytes(bad))
    assert (await read('char1')) == bytes([improv.ERROR_INVALID_RPC])
    ok('unknown command and bad checksum report errors 0x02 and 0x01')

    # A state change re-advertises while the main loop may be stopping the
    # advertisement: whichever runs first, BlueZ must end up holding exactly
    # what the service thinks it holds (Pi 3B, 2026-10-17: after a setup,
    # "Change Wi-Fi" failed with "Maximum advertisements reached").
    for stop_first in (False, True):
        await service.advertise(True)
        new_state = improv.STATE_PROVISIONED if service.state != improv.STATE_PROVISIONED else improv.STATE_AUTHORIZED
        if stop_first:
            # The stop's second call is slow, so the re-advertise runs in the
            # middle of it, as on the Pi: the stop then removes the fresh
            # Improv advertisement but not the fresh name advertisement.
            advm.unregister_delays = [0, 0.3]
            stop = service.spawn(service.advertise(False))
            await asyncio.sleep(0.05)
            service.set_state(new_state)
        else:
            service.set_state(new_state)
            stop = service.spawn(service.advertise(False))
        await asyncio.gather(stop, *list(service.tasks))
        assert bool(advm.active) == service.advertising, (stop_first, advm.active, service.advertising)
        await service.advertise(False)
        assert advm.active == {}, advm.active
    ok('a re-advertise racing a stop leaves nothing registered behind')

    # BlueZ still holding our advertisements from earlier must not block them.
    advm.active = {improv.ADV_PATH: {}, improv.NAME_ADV_PATH: {}}
    await service.advertise(True)
    assert service.advertising and set(advm.active) == {improv.ADV_PATH, improv.NAME_ADV_PATH}
    await service.advertise(False)
    ok('a stale registration of ours is cleared before advertising again')

    # Slots stuck for good: restart bluetoothd once, not in a loop.
    run = tempfile.mkdtemp(prefix='reset-')
    calls = os.path.join(run, 'calls')
    systemctl = os.path.join(run, 'systemctl')
    with open(systemctl, 'w') as f:
        f.write(f'#!/bin/sh\necho "$*" >> {calls}\n')
    os.chmod(systemctl, 0o755)
    improv.SYSTEMCTL, improv.BLUEZ_RESET_FILE = systemctl, os.path.join(run, 'reset')
    advm.refuse = True
    for _ in range(improv.ADV_FAILURES_BEFORE_RESET - 1):
        await service.update_advertising(True)
    assert not os.path.exists(calls), 'must not reset before repeated failures'
    await service.update_advertising(True)
    for _ in range(5):
        await service.update_advertising(True)
    with open(calls) as f:
        assert f.read().splitlines() == ['--no-block restart bluetooth.service']
    advm.refuse = False
    await service.update_advertising(True)
    assert service.advertising and service.adv_failures == 0
    await service.advertise(False)
    ok('BlueZ refusing every time: bluetoothd restarted once, at most every 10 minutes')


async def _eq(coro, value):
    return (await coro) == bytes(value)


async def _startswith(coro, prefix):
    return (await coro).startswith(prefix)


asyncio.run(main())
print('\nPASS Improv BLE integration tests')
