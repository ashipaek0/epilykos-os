#!/usr/bin/env python3
"""Build pinned U-Boot sandbox with actual native guard and file-backed MMC.

Needs the native-env build dependencies plus swig/Python headers/setuptools.
No network credentials, physical devices or installed images are used.
"""
import argparse
from pathlib import Path
import shutil
import sys
import tempfile

from check_native_platform_env import BOARD, ROOT, patch_source, run, source


def prepare(uboot):
    patch_source(uboot, 'uboot', '2025.10')
    for name, directory in [('epk_env.h', 'include'), ('epk_env.c', 'env'),
                            ('epk_mmc.c', 'env'), ('epk_halt.c', 'env')]:
        shutil.copyfile(BOARD / 'platform-env' / name, uboot / directory / name)
    # Test transport only: production guard remains ARM64/MMC-only. Sandbox
    # runs the identical import/core/MMC writer against its SD emulation.
    path = uboot / 'env/Kconfig'
    original = path.read_text()
    needle = 'depends on ENV_IS_IN_MMC && ENV_REDUNDANT && CMD_SAVEENV && ARM64'
    if original.count(needle) != 1:
        raise ValueError('sandbox guard dependency seam changed')
    path.write_text(original.replace(needle, needle.replace('&& ARM64', '&& (ARM64 || SANDBOX)')))
    shutil.copyfile(ROOT / 'tools/slot_boot_sandbox.c', uboot / 'cmd/epk_slot_test.c')
    path = uboot / 'cmd/Makefile'
    path.write_text(path.read_text() + '\nobj-y += epk_slot_test.o\n')
    path = uboot / 'cmd/boot.c'
    original = path.read_text()
    path.write_text(original.replace('#include <command.h>',
        '#include <command.h>\nextern int do_epk_test_reset(struct cmd_tbl *, int, int, char *const []);')
        .replace('reset, 2, 0,\tdo_reset,', 'reset, 2, 0,\tdo_epk_test_reset,'))
    path = uboot / 'drivers/mmc/sandbox_mmc.c'
    original = path.read_text()
    if 'case MMC_CMD_WRITE_SINGLE_BLOCK:' not in original:
        raise ValueError('sandbox MMC write seam changed')
    path.write_text('#include <env.h>\n' + original.replace(
        'case MMC_CMD_WRITE_MULTIPLE_BLOCK:\n',
        'case MMC_CMD_WRITE_MULTIPLE_BLOCK:\n\t\tif (env_get("test_fail_mmc_write")) return -EIO;\n'))
    path = uboot / 'board/sandbox/sandbox.c'
    path.write_text(path.read_text().replace('ENVL_NOWHERE,\n\tENVL_EXT4,\n\tENVL_FAT,', 'ENVL_MMC,'))
    path = uboot / 'arch/sandbox/dts/sandbox.dts'
    path.write_text(path.read_text() + '''
/ {
    config {
        u-boot,mmc-env-offset = <0x900000>;
        u-boot,mmc-env-offset-redundant = <0x904000>;
    };
    aliases { mmc0 = &epk_mmc; };
    epk_mmc: epilykos-mmc {
        compatible = "sandbox,mmc";
        filename = "card.img";
        bus-width = <4>;
        non-removable;
    };
};
''')


def build(uboot):
    run(['make', '-C', uboot, 'sandbox_defconfig'])
    config = [uboot / 'scripts/config', '--file', uboot / '.config']
    for option in ['VIDEO_SANDBOX_SDL', 'EFI_LOADER', 'TOOLS_MKEFICAPSULE', 'UNIT_TEST', 'CMD_UPL', 'UPL',
                   'ENV_IS_IN_EXT4', 'ENV_IS_NOWHERE', 'ENV_IMPORT_FDT', 'CMD_BOOTI']:
        run([*config, '--disable', option])
    for option in ['ENV_IS_IN_MMC', 'ENV_REDUNDANT', 'EPILYKOS_ENV_GUARD', 'CMD_EXT4', 'CMD_SAVEENV', 'MMC_WRITE']:
        run([*config, '--enable', option])
    for option, value in [('ENV_SIZE', '0x4000'), ('ENV_OFFSET', '0x400000'),
                          ('ENV_OFFSET_REDUND', '0x404000'), ('ENV_MMC_DEVICE_INDEX', '0')]:
        run([*config, '--set-val', option, value])
    run(['make', '-C', uboot, 'olddefconfig'])
    if 'CONFIG_EPILYKOS_ENV_GUARD=y' not in (uboot / '.config').read_text().splitlines():
        raise ValueError('sandbox native guard was not enabled')
    run(['make', '-C', uboot, '-j4', 'NO_SDL=1'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache-dir', type=Path, default=ROOT / 'os/build/native-env')
    args = parser.parse_args()
    cache = args.cache_dir.resolve()
    cache.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='slot-boot-', dir=cache) as directory:
        uboot = source(cache, Path(directory), 'u-boot-2025.10.tar.bz2')
        prepare(uboot)
        build(uboot)
        run([sys.executable, ROOT / 'tools/test_slot_boot.py', '--uboot', uboot / 'u-boot',
             '--mkimage', uboot / 'tools/mkimage'])
    print('PASS actual guarded U-Boot sandbox/MMC/ext4 protocol2 script tests')


if __name__ == '__main__':
    main()
