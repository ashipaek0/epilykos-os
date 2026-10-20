"""Shared app/host maintenance exclusion on the same DATA inode.

Never unlink this file. A lease is an open-file-description flock, not a PID
file or an expiring token: process death releases it without stale-lock repair.
This coordinates major jobs; monitoring writes may continue during a backup.
"""
import fcntl
import os
import stat

DEFAULT_LOCK = '/data/epilykos/app-data/.maintenance.lock'


class MaintenanceError(Exception):
    pass


class Lease:
    def __init__(self, fd):
        self.fd = fd

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None


def acquire():
    """Try once; callers report busy before starting a maintenance job."""
    path = os.environ.get('EPILYKOS_MAINTENANCE_LOCK', DEFAULT_LOCK)
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, 0o660)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise MaintenanceError('The maintenance lock must be a regular file with one link')
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise MaintenanceError('App maintenance is running. Wait for it to finish before installing an update.') from None
        # The host is root, the unprivileged container app uses gid 1000.
        # Only touch the validated inode after obtaining the lock.
        if os.geteuid() == 0:
            os.fchown(fd, 0, 1000)
        os.fchmod(fd, 0o660)
        return Lease(fd)
    except BaseException:
        os.close(fd)
        raise
