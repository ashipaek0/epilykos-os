#!/usr/bin/env python3
"""The undervoltage warning (epilykos_power): read from the hwmon alarm, remembered
by a sampler, backed by the kernel log, and part of hostd's status reply."""
import os
import stat
import sys
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True
root = Path(__file__).resolve().parent.parent
overlay = root / 'os/board/epilykos/raspberrypi3/rootfs-overlay'
failures = []


def check(cond, msg):
    if not cond:
        failures.append(msg)


with tempfile.TemporaryDirectory() as tmp:
    tmp = Path(tmp)
    hwmon = tmp / 'hwmon'
    (hwmon / 'hwmon0').mkdir(parents=True)
    (hwmon / 'hwmon0/name').write_text('cpu_thermal\n')
    dmesg = tmp / 'dmesg'
    dmesg.write_text('#!/bin/sh\ncat ' + str(tmp / 'kmsg') + '\n')
    dmesg.chmod(dmesg.stat().st_mode | stat.S_IXUSR)
    (tmp / 'kmsg').write_text('[    1.0] usb 1-1: new high-speed USB device\n')
    os.environ['EPILYKOS_HWMON_DIR'] = str(hwmon)
    os.environ['EPILYKOS_DMESG'] = str(dmesg)
    sys.path.insert(0, str(overlay / 'usr/lib/epilykos/python'))
    import epilykos_power as power

    # No rpi_volt sensor (not a Pi, or the driver is missing): nothing to show.
    check(power.status() == {'supported': False, 'undervoltage_now': False, 'undervoltage_since_boot': False, 'last_seen': None},
          f'no sensor means not supported: {power.status()}')

    (hwmon / 'hwmon1').mkdir()
    (hwmon / 'hwmon1/name').write_text('rpi_volt\n')
    alarm = hwmon / 'hwmon1/in0_lcrit_alarm'
    alarm.write_text('0\n')
    s = power.status()
    check(s['supported'] and not s['undervoltage_now'] and not s['undervoltage_since_boot'] and s['last_seen'] is None, f'fine supply: {s}')

    alarm.write_text('1\n')
    s = power.status()
    check(s['undervoltage_now'] and s['undervoltage_since_boot'] and s['last_seen'], f'low now: {s}')

    # Back to normal: it is remembered, not shown as now.
    alarm.write_text('0\n')
    s = power.status()
    check(not s['undervoltage_now'] and s['undervoltage_since_boot'] and s['last_seen'], f'normal again but it happened: {s}')

    # A brown-out before hostd started is in the kernel log.
    power._seen_at = None
    power._kernel_cache = (0.0, False)
    check(not power.status()['undervoltage_since_boot'], 'forgotten without a sampler hit or a log line')
    (tmp / 'kmsg').write_text('[   20.5] raspberrypi-hwmon soc:firmware:hwmon: Undervoltage detected!\n')
    power._kernel_cache = (0.0, False)
    s = power.status()
    check(not s['undervoltage_now'] and s['undervoltage_since_boot'] and s['last_seen'] is None, f'from the kernel log: {s}')

    # The sampler remembers a dip that was gone before anyone asked.
    power._seen_at = None
    power._kernel_cache = (0.0, False)
    (tmp / 'kmsg').write_text('')
    alarm.write_text('1\n')
    power.sample()
    alarm.write_text('0\n')
    check(power._seen_at, 'sample() remembers when it was low')

hostd = (overlay / 'usr/bin/epilykos-hostd').read_text()
check("'power': power.status()" in hostd, 'hostd status carries power')
check('power.start()' in hostd, 'hostd starts the sampler')
fragment = (root / 'os/board/epilykos/raspberrypi3/linux.fragment').read_text()
check('CONFIG_SENSORS_RASPBERRYPI_HWMON=y' in fragment, 'the kernel builds in the Raspberry Pi hwmon driver')

if failures:
    print('FAIL')
    for f in failures:
        print(' -', f)
    sys.exit(1)
print('ok: power status')
