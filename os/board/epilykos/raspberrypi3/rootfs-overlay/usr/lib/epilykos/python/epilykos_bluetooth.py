"""Bounded, device-selected BLE pairing over BlueZ's Agent1 API.

Hostd owns one D-Bus connection and its own agent; it never becomes the
system default agent. Only Pair calls from this connection use this agent.
PINs live only in the pending future and are never stored or logged.
"""
import asyncio
import os
import re
import subprocess
import threading
import time
import uuid

from dbus_fast import BusType, DBusError, Message, MessageType, Variant
from dbus_fast.aio import MessageBus
from dbus_fast.service import ServiceInterface, method

BLUEZ = 'org.bluez'
AGENT = '/org/epilykos/PairingAgent'
WINDOW_S = 300
# Tests run against a fake BlueZ on a private session bus.
BUS_TYPE = BusType.SESSION if os.environ.get('EPILYKOS_BLUEZ_BUS') == 'session' else BusType.SYSTEM


class PairingError(ValueError):
    pass


# Recovery (bluetooth_recover), asked for by the app when BlueZ is stuck. On
# the Pi 3B a reply lost on the Bluetooth UART leaves bluetoothd waiting on an
# operation that never finishes: every later connect or scan is refused with
# org.bluez.Error.InProgress until bluetoothd restarts (which also powers the
# adapter up again). At most once per RECOVER_INTERVAL_S, remembered in /run
# so neither an app nor a hostd restart can turn it into a loop.
RECOVER_FILE = os.environ.get('EPILYKOS_BLUETOOTH_RECOVER_FILE', '/run/epilykos/bluetooth-recover')
RECOVER_INTERVAL_S = 600
RECOVER_REASONS = {
    'in_progress': 'every Bluetooth device is refused with "operation already in progress"',
    'no_adapter': 'no Bluetooth adapter',
}
SYSTEMCTL = os.environ.get('EPILYKOS_SYSTEMCTL', 'systemctl')


def recover(reason):
    """Restart bluetoothd for a stuck BlueZ, unless that was done in the last
    RECOVER_INTERVAL_S. Replies whether it restarted and, if not, when it may."""
    if reason not in RECOVER_REASONS:
        raise PairingError('Unknown Bluetooth recovery reason')
    try:
        age = time.time() - os.path.getmtime(RECOVER_FILE)
        if 0 <= age < RECOVER_INTERVAL_S:
            return {'ok': True, 'restarted': False, 'retry_in': int(RECOVER_INTERVAL_S - age) + 1}
    except OSError:
        pass  # never restarted on this boot
    os.makedirs(os.path.dirname(RECOVER_FILE), exist_ok=True)
    with open(RECOVER_FILE, 'w') as f:
        f.write(f'{int(time.time())}\n')
    print(f'epilykos-hostd: Bluetooth recovery: {RECOVER_REASONS[reason]}; restarting bluetoothd', flush=True)
    subprocess.run([SYSTEMCTL, '--no-block', 'restart', 'bluetooth.service'], check=False, timeout=30)
    return {'ok': True, 'restarted': True}


async def bluez_owner(bus):
    """bluetoothd's unique bus name, or None while it is not running."""
    reply = await asyncio.wait_for(bus.call(Message(destination='org.freedesktop.DBus', path='/org/freedesktop/DBus',
                                                    interface='org.freedesktop.DBus', member='GetNameOwner',
                                                    signature='s', body=[BLUEZ])), 8)
    return reply.body[0] if reply.message_type == MessageType.METHOD_RETURN else None


async def call(bus, path, interface, member, signature='', body=()):
    reply = await asyncio.wait_for(bus.call(Message(destination=BLUEZ, path=path, interface=interface,
                                                    member=member, signature=signature, body=list(body))), 8)
    if reply.message_type == MessageType.ERROR:
        raise DBusError(reply.error_name, reply.body[0] if reply.body else '')
    return reply.body


class Agent(ServiceInterface):
    def __init__(self, owner):
        super().__init__('org.bluez.Agent1')
        self.owner = owner

    @method()
    def Release(self):
        self.owner.cancel_pending()

    @method()
    async def RequestPinCode(self, device: 'o') -> 's':
        return await self.owner.ask(device, 'pin')

    @method()
    async def RequestPasskey(self, device: 'o') -> 'u':
        return int(await self.owner.ask(device, 'passkey'))

    @method()
    async def RequestConfirmation(self, device: 'o', passkey: 'u'):
        await self.owner.ask(device, 'confirm', f'{passkey:06d}')

    @method()
    def RequestAuthorization(self, device: 'o'):
        self.owner.guard(device)

    @method()
    def AuthorizeService(self, device: 'o', service: 's'):
        self.owner.guard(device)

    @method()
    def DisplayPinCode(self, device: 'o', pincode: 's'):
        self.owner.display(device, pincode)

    @method()
    def DisplayPasskey(self, device: 'o', passkey: 'u', entered: 'q'):
        self.owner.display(device, f'{passkey:06d}')

    @method()
    def Cancel(self):
        self.owner.cancel_pending()


class Pairing:
    def __init__(self, bus):
        self.bus = bus
        self.adapter = None
        self.deadline = 0
        self.until = 0
        self.selected = None
        self.pending = None
        self.answer = None
        self.task = None
        self.result = None

    async def objects(self):
        return (await call(self.bus, '/', 'org.freedesktop.DBus.ObjectManager', 'GetManagedObjects'))[0]

    async def prop(self, name, variant):
        await call(self.bus, self.adapter, 'org.freedesktop.DBus.Properties', 'Set', 'ssv',
                   ('org.bluez.Adapter1', name, variant))

    async def init(self):
        objects = await self.objects()
        self.adapter = next((p for p, i in sorted(objects.items()) if 'org.bluez.Adapter1' in i), None)
        if not self.adapter:
            raise PairingError('No Bluetooth adapter is available')
        self.bus.export(AGENT, Agent(self))
        await call(self.bus, '/org/bluez', 'org.bluez.AgentManager1', 'RegisterAgent', 'os', (AGENT, 'KeyboardDisplay'))
        await self.prop('Pairable', Variant('b', False))

    def active(self):
        return time.monotonic() < self.deadline

    def guard(self, device):
        if not self.active() or device != self.selected:
            raise DBusError('org.bluez.Error.Rejected', 'Select this device in Epilykos settings first')

    def cancel_pending(self):
        if self.answer and not self.answer.done():
            self.answer.set_exception(DBusError('org.bluez.Error.Canceled', 'Pairing cancelled'))
        self.pending = None

    async def ask(self, device, kind, value=None):
        self.guard(device)
        if self.pending:
            raise DBusError('org.bluez.Error.Rejected', 'Another request is pending')
        self.answer = asyncio.get_running_loop().create_future()
        self.pending = {'id': uuid.uuid4().hex, 'kind': kind, 'value': value}
        try:
            return await asyncio.wait_for(self.answer, min(90, max(0, self.deadline - time.monotonic())))
        except asyncio.TimeoutError:
            raise DBusError('org.bluez.Error.Canceled', 'PIN request expired')
        finally:
            self.pending = None
            self.answer = None

    def display(self, device, value):
        self.guard(device)
        self.pending = {'id': uuid.uuid4().hex, 'kind': 'display', 'value': value}

    async def open(self):
        await self.close()
        self.result = None
        try:
            await self.prop('Powered', Variant('b', True))
            await self.prop('PairableTimeout', Variant('u', WINDOW_S))
            await self.prop('Pairable', Variant('b', True))
            await call(self.bus, self.adapter, 'org.bluez.Adapter1', 'SetDiscoveryFilter', 'a{sv}', ({'Transport': Variant('s', 'le')},))
            await call(self.bus, self.adapter, 'org.bluez.Adapter1', 'StartDiscovery')
        except Exception:
            await self.close()
            raise
        self.deadline = time.monotonic() + WINDOW_S
        self.until = int(time.time()) + WINDOW_S
        return await self.status()

    async def close(self):
        self.deadline = self.until = 0
        self.cancel_pending()
        if self.selected:
            try: await call(self.bus, self.selected, 'org.bluez.Device1', 'CancelPairing')
            except (DBusError, asyncio.TimeoutError): pass
        if self.task and not self.task.done():
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        self.selected = None
        if self.adapter:
            # Adapter's own timeout is a second guard if hostd crashes.
            try: await self.prop('Pairable', Variant('b', False))
            except (DBusError, asyncio.TimeoutError): pass  # adapter gone; PairableTimeout still ends it
            try: await call(self.bus, self.adapter, 'org.bluez.Adapter1', 'StopDiscovery')
            except (DBusError, asyncio.TimeoutError): pass  # no discovery session on this connection

    async def pair(self, address):
        if not self.active():
            raise PairingError('Open the five-minute pairing window first')
        if self.selected:
            raise PairingError('A device is already pairing')
        device = await self.device(address)
        self.selected, self.result = device, None
        self.task = asyncio.create_task(self.do_pair(device))
        return await self.status()

    async def device(self, address):
        if not isinstance(address, str) or not re.fullmatch(r'(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}', address):
            raise PairingError('Select a Bluetooth device from the list')
        for path, ifaces in (await self.objects()).items():
            props = ifaces.get('org.bluez.Device1', {})
            if props.get('Address') and props['Address'].value.upper() == address.upper() and path.startswith(self.adapter + '/'):
                return path
        raise PairingError('That device is no longer available; scan again')

    async def do_pair(self, device):
        try:
            # Pair may wait for PIN input, so use a longer timeout than status/actions.
            reply = await asyncio.wait_for(self.bus.call(Message(destination=BLUEZ, path=device,
                       interface='org.bluez.Device1', member='Pair')), min(120, max(0, self.deadline-time.monotonic())))
            if reply.message_type == MessageType.ERROR:
                raise DBusError(reply.error_name, reply.body[0] if reply.body else '')
            # Trusted: it reconnects later without asking an agent, as
            # `bluetoothctl trust` would; this one only answers in the window.
            try:
                await call(self.bus, device, 'org.freedesktop.DBus.Properties', 'Set', 'ssv',
                           ('org.bluez.Device1', 'Trusted', Variant('b', True)))
            except (DBusError, asyncio.TimeoutError):
                pass  # paired all the same
            self.result = {'ok': True, 'message': 'Paired. Select this device in your source settings.'}
        except asyncio.CancelledError:
            self.result = {'ok': False, 'message': 'Pairing cancelled'}
        except (DBusError, asyncio.TimeoutError) as e:
            self.result = {'ok': False, 'message': f'Pairing failed: {getattr(e, "type", "timed out")}. Check the device PIN and try again.'}
        finally:
            self.cancel_pending()
            self.selected = None

    async def reply(self, request_id, value):
        if not self.active() or not self.pending or self.pending['id'] != request_id or not self.answer:
            raise PairingError('That PIN request has expired')
        kind = self.pending['kind']
        if kind == 'confirm':
            if value is not True:
                self.cancel_pending()
                return await self.status()
        elif kind == 'passkey':
            if not isinstance(value, str) or not re.fullmatch(r'[0-9]{1,6}', value):
                raise PairingError('Enter a numeric passkey of up to six digits')
        elif kind == 'pin':
            if not isinstance(value, str) or not 1 <= len(value) <= 16 or not value.isascii() or not value.isprintable():
                raise PairingError('Enter a PIN of 1 to 16 characters')
        if self.answer.done():
            raise PairingError('That request was already answered')
        self.answer.set_result(value)
        return await self.status()

    async def remove(self, address):
        if self.selected:
            raise PairingError('Finish or cancel pairing before forgetting a device')
        await call(self.bus, self.adapter, 'org.bluez.Adapter1', 'RemoveDevice', 'o', (await self.device(address),))
        return await self.status()

    async def status(self):
        devices = []
        for path, ifaces in (await self.objects()).items():
            props = ifaces.get('org.bluez.Device1', {})
            if not props or not path.startswith(self.adapter + '/'):
                continue
            def val(k, default=None): return props[k].value if k in props else default
            devices.append({'address': val('Address'), 'name': val('Alias', val('Name', 'Unnamed device'))[:100],
                            'paired': val('Paired', False), 'connected': val('Connected', False)})
        return {'available': True, 'until': self.until if self.active() else 0, 'pairing': bool(self.selected),
                'pending': self.pending, 'result': self.result, 'devices': sorted(devices, key=lambda d: (not d['paired'], d['name']))[:60]}


_loop = None
_manager = None


def start():
    def thread():
        global _loop
        _loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_loop)

        async def serve():
            global _manager
            while True:
                bus = None
                try:
                    bus = await MessageBus(bus_type=BUS_TYPE).connect()
                    owner = await bluez_owner(bus)
                    manager = Pairing(bus)
                    await manager.init()
                    _manager = manager
                    # A bluetoothd restart (epilykos-improv restarts it to free stuck
                    # advertising slots) drops our agent registration: start over.
                    while bus.connected and await bluez_owner(bus) == owner:
                        if manager.until and not manager.active():
                            await manager.close()
                        await asyncio.sleep(1)
                    if bus.connected:
                        print('epilykos-hostd: bluetoothd restarted; registering the pairing agent again', flush=True)
                except Exception as e:
                    print(f'epilykos-hostd: Bluetooth pairing unavailable ({type(e).__name__})', flush=True)
                finally:
                    _manager = None
                    if bus: bus.disconnect()
                await asyncio.sleep(5)
        _loop.run_until_complete(serve())
    threading.Thread(target=thread, daemon=True).start()


def action(name, req):
    if not _manager or not _loop:
        if name == 'bluetooth_status':
            return {'ok': True, 'bluetooth': {'available': False, 'devices': []}}
        raise PairingError('Bluetooth is not ready; try again shortly')

    async def dispatch():
        m = _manager
        if name == 'bluetooth_status': out = await m.status()
        elif name == 'bluetooth_open': out = await m.open()
        elif name == 'bluetooth_close': await m.close(); out = await m.status()
        elif name == 'bluetooth_pair': out = await m.pair(req.get('address'))
        elif name == 'bluetooth_reply': out = await m.reply(req.get('id'), req.get('value'))
        elif name == 'bluetooth_remove': out = await m.remove(req.get('address'))
        else: raise PairingError('Unknown Bluetooth action')
        return {'ok': True, 'bluetooth': out}
    future = asyncio.run_coroutine_threadsafe(dispatch(), _loop)
    try:
        return future.result(timeout=12)
    except TimeoutError:
        future.cancel()
        raise PairingError('Bluetooth did not answer in time')
    except DBusError as e:
        raise PairingError(f'Bluetooth refused the action ({e.type})')
