"""The Pi's undervoltage warning, as Settings shows it.

The firmware tells the kernel when the supply drops under about 4.63 V; the
raspberrypi-hwmon driver shows it as in0_lcrit_alarm (1 while it is low) and
logs "Undervoltage detected!" / "Voltage normalised". Read-only.

The alarm is only the present moment, so a sampler remembers that it was low:
a brown-out of a few seconds is what corrupts SD cards and drops Bluetooth,
and it is gone by the time anyone looks. The kernel log covers the time
before hostd started.
"""
import os
import subprocess
import threading
import time

HWMON_DIR = os.environ.get('EPILYKOS_HWMON_DIR', '/sys/class/hwmon')
DMESG = os.environ.get('EPILYKOS_DMESG', 'dmesg')
SAMPLE_S = 5.0

_lock = threading.Lock()
_seen_at = None


def _alarm_files():
    try:
        names = sorted(os.listdir(HWMON_DIR))
    except OSError:
        return []
    out = []
    for name in names:
        base = os.path.join(HWMON_DIR, name)
        try:
            with open(os.path.join(base, 'name')) as f:
                if f.read().strip() != 'rpi_volt':
                    continue
        except OSError:
            continue
        out.append(os.path.join(base, 'in0_lcrit_alarm'))
    return out


def low_now():
    """True while the supply is low, False when fine, None when the box cannot tell."""
    files = _alarm_files()
    if not files:
        return None
    for path in files:
        try:
            with open(path) as f:
                if f.read().strip() == '1':
                    return True
        except OSError:
            continue
    return False


_kernel_cache = (0.0, False)


def _kernel_saw_it():
    """Did the kernel log it? A yes is final; a no is rechecked after a minute."""
    global _kernel_cache
    at, found = _kernel_cache
    if found or time.monotonic() - at < 60:
        return found
    found = _read_kernel_log()
    _kernel_cache = (time.monotonic(), found)
    return found


def _read_kernel_log():
    try:
        out = subprocess.run([DMESG], capture_output=True, text=True, timeout=3, check=False).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return 'undervoltage detected' in out.lower()


def sample():
    """Look once; remember when it was low."""
    global _seen_at
    if low_now():
        with _lock:
            _seen_at = int(time.time())


def start():
    def loop():
        while True:
            sample()
            time.sleep(SAMPLE_S)
    threading.Thread(target=loop, daemon=True).start()


def status():
    """{'supported', 'undervoltage_now', 'undervoltage_since_boot', 'last_seen'}"""
    now = low_now()
    if now is None:
        return {'supported': False, 'undervoltage_now': False, 'undervoltage_since_boot': False, 'last_seen': None}
    if now:
        sample()
    with _lock:
        seen = _seen_at
    return {'supported': True, 'undervoltage_now': now,
            'undervoltage_since_boot': bool(now or seen or _kernel_saw_it()), 'last_seen': seen}
