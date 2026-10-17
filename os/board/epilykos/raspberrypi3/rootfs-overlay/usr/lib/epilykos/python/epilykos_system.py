"""Timezone, clock and backing-card state for C-TIME-001 / D-STORAGE-001."""
import json
import os
import re
import subprocess
import time
from pathlib import Path

DATA = Path(os.environ.get('EPILYKOS_SYSTEM_DATA', '/data/epilykos'))
RUN = Path(os.environ.get('EPILYKOS_RUN_DIR', '/run/epilykos'))
ZONES = Path(os.environ.get('EPILYKOS_ZONEINFO', '/usr/share/zoneinfo'))
SYS = Path(os.environ.get('EPILYKOS_SYS_BLOCK', '/sys/class/block'))


class SystemError(ValueError):
    pass


def validate_zone(zone):
    if not isinstance(zone, str) or not re.fullmatch(r'[A-Za-z0-9_+/-]{1,80}', zone) or '..' in zone:
        raise SystemError('Enter an IANA time zone, such as Africa/Lagos')
    f = ZONES / zone
    try:
        if not f.resolve().is_relative_to(ZONES.resolve()) or f.read_bytes()[:4] != b'TZif':
            raise OSError()
    except OSError:
        raise SystemError('That time zone is not available')
    return zone


def timezone():
    try:
        return validate_zone((DATA / 'timezone').read_text().strip())
    except (OSError, SystemError):
        return 'UTC'


def set_timezone(zone):
    zone = validate_zone(zone)
    DATA.mkdir(parents=True, exist_ok=True)
    tmp = DATA / 'timezone.tmp'
    with tmp.open('w') as f:
        f.write(zone + '\n'); f.flush(); os.fsync(f.fileno())
    tmp.replace(DATA / 'timezone')
    return zone


def clock():
    # systemd-timesyncd creates this only after synchronization. Missing means unknown/unsynced.
    return {'epoch': int(time.time()), 'synchronized': Path('/run/systemd/timesync/synchronized').exists(),
            'timezone': timezone()}


def write_clock():
    RUN.mkdir(parents=True, exist_ok=True)
    tmp = RUN / 'clock.json.tmp'
    tmp.write_text(json.dumps(clock()) + '\n')
    tmp.replace(RUN / 'clock.json')


def set_clock(epoch):
    if isinstance(epoch, bool) or not isinstance(epoch, (int, float)) or not 1577836800 <= epoch <= 4102444800:
        raise SystemError('Choose a time between 2020 and 2100')
    # Do not silently fight the NTP client. It remains enabled and may correct this later.
    try:
        subprocess.run(['date', '-u', '-s', '@' + str(int(epoch))], check=True, capture_output=True, timeout=5)
    except subprocess.SubprocessError:
        raise SystemError('The host could not set its clock')
    # Persist the corrected value for the next offline boot, just as timesyncd does.
    stamp = Path('/data/timesync/clock')
    stamp.parent.mkdir(parents=True, exist_ok=True)
    stamp.touch(); os.utime(stamp, (epoch, epoch))
    Path('/run/systemd/timesync/synchronized').unlink(missing_ok=True)
    write_clock()
    return clock()


def storage(source=None):
    try:
        if source is None:
            source = subprocess.check_output(['findmnt', '-n', '-o', 'SOURCE', '/data'], text=True, timeout=3).strip()
        name = Path(source).name
        if not re.fullmatch(r'[A-Za-z0-9_-]+', name):
            return None
        dev = SYS / name
        if (dev / 'partition').exists():
            dev = dev.resolve().parent
        size = int((dev / 'size').read_text().strip()) * 512  # sysfs always uses 512-byte sectors
        # Marketed 32 GB cards can have slightly less than 32e9 usable bytes.
        return {'device': dev.name, 'bytes': size, 'undersized': size < 31_000_000_000,
                'minimum_gb': 32, 'recommended_gb': 64}
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
