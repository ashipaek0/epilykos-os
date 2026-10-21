#!/usr/bin/env python3
"""Real flock behavior and failure cleanup, no external app/network needed."""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'os/board/epilykos/raspberrypi3/rootfs-overlay/usr/lib/epilykos/python'))
import epilykos_maintenance as maintenance
import epilykos_update as update


class SharedMaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.file = Path(self.tmp.name) / 'maintenance.lock'
        self.env = patch.dict(os.environ, EPILYKOS_MAINTENANCE_LOCK=str(self.file))
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def probe(self):
        return subprocess.run(['flock', '-n', str(self.file), '-c', 'true']).returncode

    def test_host_lease_excludes_another_process_then_releases(self):
        lease = maintenance.acquire()
        inode = self.file.stat().st_ino
        try:
            self.assertEqual(self.probe(), 1)
            with self.assertRaises(maintenance.MaintenanceError):
                maintenance.acquire()
        finally:
            lease.close()
        lease.close()
        self.assertEqual(self.probe(), 0)
        self.assertEqual(self.file.stat().st_ino, inode)
        self.assertEqual(self.file.stat().st_mode & 0o777, 0o660)

    def test_symlink_and_fifo_are_rejected_without_modifying_a_target(self):
        target = Path(self.tmp.name) / 'private'
        target.write_text('private')
        target.chmod(0o600)
        self.file.symlink_to(target)
        with self.assertRaises(OSError):
            maintenance.acquire()
        self.assertEqual(target.read_text(), 'private')
        self.assertEqual(target.stat().st_mode & 0o777, 0o600)
        self.file.unlink()
        os.mkfifo(self.file)
        with self.assertRaises(maintenance.MaintenanceError):
            maintenance.acquire()

    def test_hard_link_is_rejected_before_host_changes_permissions(self):
        target = Path(self.tmp.name) / 'private'
        target.write_text('private')
        target.chmod(0o600)
        os.link(target, self.file)
        with self.assertRaises(maintenance.MaintenanceError):
            maintenance.acquire()
        self.assertEqual(target.stat().st_mode & 0o777, 0o600)

    def test_metadata_failure_in_update_worker_still_releases_shared_lease(self):
        lease = maintenance.acquire()
        update._running.set()
        try:
            with patch.object(update, 'current', side_effect=OSError('unreadable state')), \
                 patch.object(update, 'UPDATE_DIR', str(Path(self.tmp.name) / 'updater')):
                try:
                    update._run({'release': 'test'}, {}, lease)
                except OSError:
                    pass
            self.assertEqual(self.probe(), 0)
            self.assertFalse(update._running.is_set())
        finally:
            lease.close()
            update._running.clear()


if __name__ == '__main__':
    unittest.main()
