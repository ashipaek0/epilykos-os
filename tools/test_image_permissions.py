#!/usr/bin/env python3
"""Exercise the real post-build hook and unprivileged filesystem staging cleanup."""
import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BOARD = ROOT / 'os/board/epilykos/raspberrypi3'


@unittest.skipIf(os.geteuid() == 0, 'staging cleanup must run as an ordinary build user')
class ImagePermissions(unittest.TestCase):
    def exercise(self, initial_mode):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            target = base / 'target'
            bluez = target / 'etc/bluetooth'
            bluez.mkdir(parents=True)
            (bluez / 'main.conf').write_text('[General]\nControllerMode = le\n')
            (bluez / 'network.conf').write_text('[General]\n')
            bluez.chmod(initial_mode)
            (target / 'etc/sudoers.d').mkdir()
            (target / 'etc/sudoers').write_text('# fixture\n')
            (target / 'etc/sudoers.d/epilykos').write_text('# fixture\n')
            (target / 'usr/lib').mkdir(parents=True)
            commands = base / 'bin'
            commands.mkdir()
            # Only the registry is stubbed: run the real board hook and real
            # copy/removal operations, without downloading an application image.
            skopeo = commands / 'skopeo'
            skopeo.write_text('''#!/bin/sh
set -eu
case "$1" in
  copy)
    for arg do
      case "$arg" in
        oci-archive:*) archive=${arg#oci-archive:}; archive=${archive%%:*};
          printf 'fixture image' > "$archive" ;;
      esac
    done ;;
  inspect) printf '{"architecture":"arm64"}' ;;
  *) exit 1 ;;
esac
''')
            skopeo.chmod(0o755)
            env = dict(os.environ, TARGET_DIR=str(target), EPILYKOS_CHANNEL='dev',
                       EPILYKOS_OS_VERSION='permissions-test',
                       PATH=str(commands) + os.pathsep + os.environ['PATH'])
            staged = base / 'filesystem-target'
            try:
                result = subprocess.run(['sh', str(BOARD / 'post-build.sh')], env=env,
                                        text=True, capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                # Buildroot fs/common.mk makes a mode-preserving rootfs copy,
                # then removes that copy outside fakeroot after image creation.
                shutil.copytree(target, staged)
                mode = stat.S_IMODE((staged / 'etc/bluetooth').stat().st_mode)
                result = subprocess.run(['rm', '-rf', str(staged)], text=True,
                                        capture_output=True)
                self.assertEqual(result.returncode, 0,
                                 'Buildroot cannot clean its staging copy: ' + result.stderr)
                self.assertFalse(staged.exists())
                self.assertEqual(mode, 0o755)
                self.assertEqual(stat.S_IMODE((target / 'etc/sudoers.d/epilykos').stat().st_mode), 0o440)
                self.assertIn('/dev/root                    /              auto    ro',
                              (target / 'etc/fstab').read_text())
                dropin = BOARD / 'rootfs-overlay/usr/lib/systemd/system/bluetooth.service.d/20-read-only-root.conf'
                self.assertIn('ConfigurationDirectoryMode=0755', dropin.read_text())
            finally:
                # Even the intentionally failing pre-fix run must be removable.
                for tree in (target, staged):
                    for directory, _, _ in os.walk(tree):
                        Path(directory).chmod(0o755)

    def test_clean_build_staging_can_be_removed(self):
        self.exercise(0o755)

    def test_incremental_build_repairs_old_nonwritable_directory(self):
        self.exercise(0o555)


if __name__ == '__main__':
    unittest.main()
