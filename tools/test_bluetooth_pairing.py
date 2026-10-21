#!/usr/bin/env python3
"""Real Agent1 methods and BlueZ messages against fake services on a private D-Bus."""
import asyncio
import os
import sys
import time
from pathlib import Path
sys.dont_write_bytecode=True
if 'DBUS_SESSION_BUS_ADDRESS' not in os.environ:
    os.execvp('dbus-run-session',['dbus-run-session','--',sys.executable,__file__])
root=Path(__file__).resolve().parent.parent
os.environ['EPILYKOS_BLUEZ_BUS']='session'  # hostd's own loop (bt.start) talks to the fake BlueZ below
sys.path.insert(0,str(root/'os/board/epilykos/raspberrypi3/rootfs-overlay/usr/lib/epilykos/python'))
import epilykos_bluetooth as bt
from dbus_fast import BusType, Message, MessageType, Variant
from dbus_fast.aio import MessageBus
from dbus_fast.constants import PropertyAccess
from dbus_fast.service import ServiceInterface, method, dbus_property

ADAPTER='/org/bluez/hci0'
ADDRESS='AA:BB:CC:DD:EE:FF'
DEVICE=ADAPTER+'/dev_AA_BB_CC_DD_EE_FF'

class Adapter(ServiceInterface):
    def __init__(self):
        super().__init__('org.bluez.Adapter1');self.pairable=False;self.powered=True;self.timeout=0;self.scanning=False
    @dbus_property()
    def Pairable(self)->'b': return self.pairable
    @Pairable.setter
    def Pairable(self,v:'b'): self.pairable=v
    @dbus_property()
    def Powered(self)->'b': return self.powered
    @Powered.setter
    def Powered(self,v:'b'): self.powered=v
    @dbus_property()
    def PairableTimeout(self)->'u': return self.timeout
    @PairableTimeout.setter
    def PairableTimeout(self,v:'u'):self.timeout=v
    @method()
    def SetDiscoveryFilter(self,options:'a{sv}'):
        assert options['Transport'].value=='le'
    @method()
    def StartDiscovery(self):self.scanning=True
    @method()
    def StopDiscovery(self):self.scanning=False
    @method()
    def RemoveDevice(self,path:'o'):
        assert path==DEVICE;device.paired=False

class AgentManager(ServiceInterface):
    def __init__(self):super().__init__('org.bluez.AgentManager1');self.capability=None;self.registered=0
    @method()
    def RegisterAgent(self,path:'o',capability:'s'):
        assert path==bt.AGENT;self.capability=capability;self.registered+=1

class Device(ServiceInterface):
    def __init__(self):super().__init__('org.bluez.Device1');self.paired=False;self.mode='RequestPasskey';self.pin=None;self.cancelled=0
    @dbus_property(access=PropertyAccess.READ)
    def Address(self)->'s':return ADDRESS
    @dbus_property(access=PropertyAccess.READ)
    def Alias(self)->'s':return 'PIN inverter'
    @dbus_property(access=PropertyAccess.READ)
    def Paired(self)->'b':return self.paired
    @dbus_property(access=PropertyAccess.READ)
    def Connected(self)->'b':return False
    @method()
    async def Pair(self):
        signature='ou' if self.mode=='RequestConfirmation' else 'o'
        body=[DEVICE,123] if signature=='ou' else [DEVICE]
        reply=await bluez.call(Message(destination=app.unique_name,path=bt.AGENT,interface='org.bluez.Agent1',member=self.mode,signature=signature,body=body))
        if reply.message_type==MessageType.ERROR:raise bt.DBusError(reply.error_name,reply.body[0])
        self.pin=reply.body[0] if reply.body else None;self.paired=True
    @method()
    def CancelPairing(self):self.cancelled+=1

device=Device()
async def wait(predicate):
    for _ in range(200):
        if predicate():return
        await asyncio.sleep(.01)
    raise AssertionError('timed out waiting for agent request')

async def main():
    global app,bluez
    app=await MessageBus(bus_type=BusType.SESSION).connect();bluez=await MessageBus(bus_type=BusType.SESSION).connect()
    adapter=Adapter();manager=AgentManager()
    bluez.export(ADAPTER,adapter);bluez.export(DEVICE,device);bluez.export('/org/bluez',manager)
    await bluez.request_name('org.bluez')
    m=bt.Pairing(app);await m.init();assert not adapter.pairable and manager.capability=='KeyboardDisplay'
    try:await m.pair(ADDRESS)
    except bt.PairingError:pass
    else:raise AssertionError('pairing outside window allowed')
    state=await m.open();assert state['until']>time.time() and adapter.pairable and adapter.timeout==300 and adapter.scanning
    # A different device cannot use the agent, even while the adapter is pairable.
    reply=await bluez.call(Message(destination=app.unique_name,path=bt.AGENT,interface='org.bluez.Agent1',member='RequestAuthorization',signature='o',body=[ADAPTER+'/dev_11_22_33_44_55_66']))
    assert reply.message_type==MessageType.ERROR
    for mode,pin in [('RequestPasskey','001234'),('RequestPinCode','ab12'),('RequestConfirmation',True)]:
        device.paired=False;device.mode=mode
        await m.pair(ADDRESS);await wait(lambda:m.pending is not None)
        pending=m.pending.copy();assert pending['kind'] in ('pin','passkey','confirm')
        try:await m.reply('stale',pin)
        except bt.PairingError:pass
        else:raise AssertionError('stale prompt accepted')
        if mode=='RequestPasskey':
            try:await m.reply(pending['id'],'abc')
            except bt.PairingError:pass
            else:raise AssertionError('invalid passkey accepted')
        await m.reply(pending['id'],pin);await m.task
        assert m.result['ok'] and device.paired
        if mode=='RequestPasskey':assert device.pin==1234
        if mode=='RequestPinCode':assert device.pin=='ab12'
    await m.close();assert not adapter.pairable and not adapter.scanning and device.paired, 'closing preserves bonds'
    await m.remove(ADDRESS);assert not device.paired
    await m.open();device.mode='RequestPasskey';await m.pair(ADDRESS);await wait(lambda:m.pending is not None)
    pending=m.pending.copy();m.deadline=time.monotonic()-1
    try:await m.reply(pending['id'],'1234')
    except bt.PairingError:pass
    else:raise AssertionError('expired window accepted PIN')
    await m.close();assert not adapter.pairable and m.pending is None and not m.selected and device.cancelled
    app.disconnect()

    # hostd's background loop: registers the agent, and again after a
    # bluetoothd restart (a new owner of org.bluez), which drops it.
    before=manager.registered
    bt.start()
    for _ in range(300):
        if bt._manager is not None and manager.registered>before:break
        await asyncio.sleep(.02)
    assert bt._manager is not None and manager.registered==before+1, 'hostd registers its agent at start'
    assert (await asyncio.to_thread(bt.action,'bluetooth_status',{}))['bluetooth']['available']
    await bluez.release_name('org.bluez');bluez.disconnect()
    bluez2=await MessageBus(bus_type=BusType.SESSION).connect();manager2=AgentManager()
    bluez2.export(ADAPTER,Adapter());bluez2.export('/org/bluez',manager2)
    await bluez2.request_name('org.bluez')
    for _ in range(600):
        if manager2.registered:break
        await asyncio.sleep(.02)
    assert manager2.registered==1, 'after a bluetoothd restart the agent is registered again'
    bluez2.disconnect()
    print('PASS Bluetooth pairing: Agent1 registration, selected-device authorization, PIN/passkey/confirmation, stale/expired rejection, cancel, forget, retained bonds, re-registration after a bluetoothd restart')
asyncio.run(main())
