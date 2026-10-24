"""DATA space policy shared by app maintenance and OS updates (R09; OS update
design §8.1). The same thresholds as the app's modules/storageStatus.js:

  floor     max(10 % of DATA, 1 GiB)  projected free space never goes below it
  warning   max(20 % of DATA, 2 GiB)  Settings warns below it

Nothing is ever deleted to make room: the database, backups and images
stay; the job is refused or stopped with a clear error instead.
"""
import os

GIB = 1024 ** 3
DATA = os.environ.get('EPILYKOS_DATA_DIR', '/data')


class StorageError(Exception):
    """A sentence Settings can show."""


def usage(path=None):
    """(total, free) bytes of the DATA filesystem, as root sees it."""
    st = os.statvfs(path or DATA)
    return st.f_blocks * st.f_frsize, st.f_bavail * st.f_frsize


def floor(total):
    return max(GIB, -(-total // 10))


def warning(total):
    return max(2 * GIB, -(-total // 5))


def admit(required, path=None):
    """Refuse when `required` more bytes would take DATA below the floor."""
    total, free = usage(path)
    limit = floor(total)
    result = {'total_bytes': total, 'free_bytes': free, 'required_bytes': required, 'floor_bytes': limit,
              'low': free < warning(total)}
    if free - required < limit:
        short = limit - (free - required)
        raise StorageError(f'Not enough free space on DATA: this needs {gib(required)} and must leave '
                           f'{gib(limit)} free; free up {gib(short)} first')
    return result


def check_headroom(path=None):
    """While a transfer runs: stop before DATA falls below the floor."""
    total, free = usage(path)
    if free < floor(total):
        raise StorageError(f'DATA is nearly full (less than {gib(floor(total))} free), so the download stopped')
    return free


def gib(n):
    return f'{n / GIB:.1f} GiB'
