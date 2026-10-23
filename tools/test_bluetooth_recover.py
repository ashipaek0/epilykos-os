#!/usr/bin/env python3
"""Bluetooth on the Pi 3B (C-RUNTIME-003): the UART speed cap and the
bluetooth_recover host action.

- The kernel patch adds hci_uart.max_speed, the defconfig applies the patch
  directory, and modprobe.d sets 921600 baud.
- bluetooth_recover restarts bluetoothd for a known reason, at most once per
  10 minutes (remembered in /run), and hostd routes the action to it.
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
check('BR2_LINUX_KERNEL_PATCH="$(BR2_EXTERNAL_EPILYKOS_PATH)/board/epilykos/raspberrypi3/patches/linux"' in defconfig,
      'defconfig applies the board kernel patches')
fragment = (board / 'linux.fragment').read_text()
check('CONFIG_BT_HCIUART=m' in fragment and 'CONFIG_BT_HCIUART_BCM=y' in fragment,
      'hci_bcm is built into the hci_uart module, so the option is hci_uart.max_speed')
conf = (overlay / 'etc/modprobe.d/epilykos-bluetooth.conf').read_text()
check(re.search(r'^options hci_uart max_speed=921600$', conf, re.M), 'modprobe.d sets hci_uart max_speed=921600')

# -- bluetooth_recover ---------------------------------------------------------------
with tempfile.TemporaryDirectory() as tmp:
    calls = Path(tmp) / 'calls'
    fake = Path(tmp) / 'systemctl'
    fake.write_text(f'#!/bin/sh\necho "$@" >> {calls}\n')
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    os.environ['EPILYKOS_SYSTEMCTL'] = str(fake)
    os.environ['EPILYKOS_BLUETOOTH_RECOVER_FILE'] = str(Path(tmp) / 'run/epilykos/bluetooth-recover')
    sys.path.insert(0, str(overlay / 'usr/lib/epilykos/python'))
    import epilykos_bluetooth as bt

    first = bt.recover('in_progress')
    check(first == {'ok': True, 'restarted': True}, f'first recovery restarts bluetoothd: {first}')
    check(calls.read_text().split('\n')[0] == '--no-block restart bluetooth.service', 'restarts bluetooth.service without waiting')
    again = bt.recover('no_adapter')
    check(again['ok'] and again['restarted'] is False and 0 < again['retry_in'] <= 600, f'second within 10 min is refused: {again}')
    check(len(calls.read_text().strip().split('\n')) == 1, 'no second restart within 10 minutes')
    old = time.time() - 601
    os.utime(os.environ['EPILYKOS_BLUETOOTH_RECOVER_FILE'], (old, old))
    check(bt.recover('no_adapter')['restarted'] is True, 'allowed again after 10 minutes')
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
