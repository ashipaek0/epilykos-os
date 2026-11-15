#!/usr/bin/env python3
"""Bluetooth on the Pi 3B (C-RUNTIME-003): the UART speed cap and the
bluetooth_recover host action.

- The kernel patch adds hci_uart.max_speed, the defconfig applies the patch
  directory, and modprobe.d sets 921600 baud.
- bluetooth_recover acts on the layer that failed, at most once per 10 minutes
  (remembered in /run): no adapter in the kernel reloads the chip's driver,
  anything else restarts bluetoothd. hostd routes the action to it.
"""
import os
import re
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.dont_write_bytecode = True
root = Path(__file__).resolve().parent.parent
board = root / 'os/board/epilykos/raspberrypi3'
overlay = board / 'rootfs-overlay'

failures = []


def check(cond, msg):
    if not cond:
        failures.append(msg)


# -- UART speed cap ------------------------------------------------------------------
patch = (board / 'patches/linux/0001-Bluetooth-hci_bcm-max_speed-module-option.patch').read_text()
check('+module_param(max_speed, uint, 0444);' in patch, 'patch adds the max_speed module option')
check(re.search(r'^\+\s+if \(max_speed && bcmdev->oper_speed > max_speed\) \{$', patch, re.M), 'patch caps oper_speed in serdev probe')
check(patch.count('--- a/drivers/bluetooth/hci_bcm.c\n') == 1 and patch.count('+++ b/drivers/bluetooth/hci_bcm.c\n') == 1, 'patch is -p1 against hci_bcm.c')
defconfig = (root / 'os/configs/epilykos_raspberrypi3_defconfig').read_text()
check('BR2_GLOBAL_PATCH_DIR="$(BR2_EXTERNAL_EPILYKOS_PATH)/board/epilykos/raspberrypi3/patches"' in defconfig,
      'the global patch directory applies patches/linux to the kernel')
# Buildroot applies the global directory and BR2_LINUX_KERNEL_PATCH both; the
# same patch twice stops the build ("duplicate filename").
check(not re.search(r'^BR2_LINUX_KERNEL_PATCH=', defconfig, re.M), 'kernel patches are not also listed in BR2_LINUX_KERNEL_PATCH')
fragment = (board / 'linux.fragment').read_text()
check('CONFIG_BT_HCIUART=m' in fragment and 'CONFIG_BT_HCIUART_BCM=y' in fragment,
      'hci_bcm is built into the hci_uart module, so the option is hci_uart.max_speed')
conf = (overlay / 'etc/modprobe.d/epilykos-bluetooth.conf').read_text()
check(re.search(r'^options hci_uart max_speed=921600$', conf, re.M), 'modprobe.d sets hci_uart max_speed=921600')

# -- bluetooth_recover ---------------------------------------------------------------
# Two layers: no adapter in the kernel means the chip failed to start up, so its
# driver is reloaded; anything else is bluetoothd, which is restarted.
with tempfile.TemporaryDirectory() as tmp:
    tmp = Path(tmp)
    calls = tmp / 'calls'
    sysbt = tmp / 'sys-bluetooth'
    sysbt.mkdir()
    # Fake tools log every call; the fake modprobe brings hci0 back on load when asked to.
    systemctl = tmp / 'systemctl'
    systemctl.write_text(f'#!/bin/sh\necho "systemctl $@" >> {calls}\n')
    modprobe = tmp / 'modprobe'
    modprobe.write_text(f'#!/bin/sh\necho "modprobe $@" >> {calls}\n'
                        f'if [ "$1" != "-r" ] && [ -e {tmp}/chip-ok ]; then mkdir -p {sysbt}/hci0; fi\n')
    for f in (systemctl, modprobe):
        f.chmod(f.stat().st_mode | stat.S_IXUSR)
    os.environ['EPILYKOS_SYSTEMCTL'] = str(systemctl)
    os.environ['EPILYKOS_MODPROBE'] = str(modprobe)
    os.environ['EPILYKOS_SYS_BLUETOOTH'] = str(sysbt)
    os.environ['EPILYKOS_BLUETOOTH_RECOVER_FILE'] = str(tmp / 'run/epilykos/bluetooth-recover')
    sys.path.insert(0, str(overlay / 'usr/lib/epilykos/python'))
    import epilykos_bluetooth as bt
    bt.UNLOAD_PAUSE_S = 0
    bt.ADAPTER_WAIT_S = 1

    def log():
        return calls.read_text().strip().split('\n') if calls.exists() else []

    def reset():
        calls.unlink(missing_ok=True)
        Path(os.environ['EPILYKOS_BLUETOOTH_RECOVER_FILE']).unlink(missing_ok=True)

    # in_progress: the chip is fine, bluetoothd is restarted.
    (sysbt / 'hci0').mkdir()
    first = bt.recover('in_progress', background=False)
    check(first == {'ok': True, 'restarted': True, 'layer': 'service'}, f'in_progress restarts bluetoothd: {first}')
    check(log() == ['systemctl --no-block restart bluetooth.service'], f'in_progress touches only the service: {log()}')
    again = bt.recover('no_adapter', background=False)
    check(again['ok'] and again['restarted'] is False and 0 < again['retry_in'] <= 600, f'second within 10 min is refused: {again}')
    check(len(log()) == 1, 'no second action within 10 minutes')
    old = time.time() - 601
    os.utime(os.environ['EPILYKOS_BLUETOOTH_RECOVER_FILE'], (old, old))
    check(bt.recover('no_adapter', background=False)['layer'] == 'service',
          'no_adapter with an adapter in the kernel is only bluetoothd: restart it')

    # no_adapter and none in the kernel: reload the chip's driver, then start bluetoothd.
    reset()
    (sysbt / 'hci0').rmdir()
    (tmp / 'chip-ok').write_text('')
    r = bt.recover('no_adapter', background=False)
    check(r == {'ok': True, 'restarted': True, 'layer': 'driver'}, f'no adapter in the kernel reloads the driver: {r}')
    check(log() == ['systemctl stop bluetooth.service', 'modprobe -r hci_uart', 'modprobe hci_uart', 'systemctl start bluetooth.service'],
          f'driver reload order: stop bluetoothd, unload, load, start bluetoothd: {log()}')
    check(not any('restart' in c for c in log()), 'restarting bluetoothd is not the fix for a chip that did not start')

    # The adapter never comes back: still starts bluetoothd, and says so.
    reset()
    (sysbt / 'hci0').rmdir()
    (tmp / 'chip-ok').unlink()
    started = time.monotonic()
    check(bt.recover('no_adapter', background=False)['layer'] == 'driver', 'a dead chip is still tried once')
    check(log()[-1] == 'systemctl start bluetooth.service' and time.monotonic() - started < 5, 'gives up after the wait and starts bluetoothd')

    # The reload runs in the background, so hostd answers at once.
    reset()
    bt.ADAPTER_WAIT_S = 0.2
    r = bt.recover('no_adapter')
    check(r['layer'] == 'driver', 'background reload replies with the layer')
    for _ in range(40):
        if log() and log()[-1] == 'systemctl start bluetooth.service':
            break
        time.sleep(0.1)
    check(log()[-1:] == ['systemctl start bluetooth.service'], f'the background reload finishes: {log()}')

    try:
        bt.recover('reboot')
        check(False, 'an unknown reason is refused')
    except bt.PairingError:
        pass

hostd = (overlay / 'usr/bin/epilykos-hostd').read_text()
check("if action == 'bluetooth_recover':\n        return bluetooth.recover(req.get('reason'))" in hostd, 'hostd routes bluetooth_recover')
check('{"action": "bluetooth_recover", "reason": "in_progress"}' in hostd, 'hostd documents bluetooth_recover')
check(hostd.index("action == 'bluetooth_recover'") < hostd.index('if action in BLUETOOTH_ACTIONS'),
      'recovery does not need the pairing manager (works while BlueZ is down)')

if failures:
    for f in failures:
        print('FAIL', f)
    sys.exit(1)
print('test_bluetooth_recover: UART speed cap and bluetooth_recover OK')
