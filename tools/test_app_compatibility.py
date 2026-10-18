#!/usr/bin/env python3
import copy
import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'os/board/epilykos/raspberrypi3/rootfs-overlay/usr/lib/epilykos/python'))
import epilykos_app_compat as compat
import epilykos_update as update

RUNNING = {'Config': {'Labels': {compat.LABEL: compat.PROTOCOL}, 'Env': [compat.LOCK_ENV]},
           'State': {'Running': True},
           'Mounts': [{'Type': 'bind', 'Source': '/data/epilykos/app-data', 'Destination': '/app/data', 'RW': True}]}


class AppCompatibilityTests(unittest.TestCase):
    def test_shadowed_or_mismatched_lock_mount_is_refused(self):
        for mount in [dict(RUNNING['Mounts'][0], Source='/somewhere/else'),
                      dict(RUNNING['Mounts'][0], RW=False), {'Destination': '/app/data/.maintenance.lock'}]:
            info = copy.deepcopy(RUNNING)
            info['Mounts'] = [RUNNING['Mounts'][0], mount] if mount.get('Destination') == '/app/data/.maintenance.lock' else [mount]
            with self.subTest(mount=mount), self.assertRaises(compat.CompatibilityError):
                compat.running_container(info)

    def test_malformed_mount_collection_is_a_readable_rejection(self):
        info = copy.deepcopy(RUNNING)
        info['Mounts'] = 123
        with self.assertRaises(compat.CompatibilityError):
            compat.running_container(info)

    def test_duplicate_lock_environment_is_refused(self):
        info = copy.deepcopy(RUNNING)
        info['Config']['Env'].append('EPILYKOS_MAINTENANCE_LOCK=/different/inode')
        with self.assertRaises(compat.CompatibilityError):
            compat.running_container(info)

    def test_malformed_or_ambiguous_inspection_is_refused(self):
        for raw in ['{}', '[]', '[{}, {}]', 'not json', json.dumps([123]), '[' * 5000]:
            with self.subTest(raw=raw[:20]), self.assertRaises(compat.CompatibilityError):
                compat.inspection(raw)

    def test_inspection_timeout_returns_update_error(self):
        with patch.object(update.subprocess, 'run', side_effect=subprocess.TimeoutExpired('podman', 30)):
            with self.assertRaises(update.UpdateError):
                update._inspect_app('container', 'epilykos')


if __name__ == '__main__':
    unittest.main()
