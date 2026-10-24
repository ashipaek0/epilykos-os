#!/usr/bin/env python3
"""Check every RAUC system.conf in the OS tree against the keys the pinned
RAUC release accepts. RAUC refuses to start on an unknown key (a typo such
as "mount-prefix" for "mountprefix" stops rauc.service, and with it the
boot-health confirmation), and that only shows up on a booted image.

Key lists are from RAUC 1.15.2 (src/config_file.c, docs/reference.rst);
update them together with BR2_PACKAGE_RAUC's version.
"""
import configparser
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAUC_VERSION = '1.15.2'
SYSTEM_KEYS = {
    'activate-installed', 'barebox-dtbpath', 'barebox-statename', 'boot-attempts', 'boot-attempts-primary',
    'bootloader', 'bundle-formats', 'compatible', 'data-directory', 'efi-use-bootnext', 'grubenv',
    'max-bundle-download-size', 'max-bundle-signature-size', 'min-bundle-version', 'mountprefix',
    'perform-pre-check', 'prevent-late-fallback', 'statusfile', 'variant-dtb', 'variant-file', 'variant-name',
}
SLOT_KEYS = {
    'allow-mounted', 'bootname', 'description', 'device', 'extra-mkfs-opts', 'extra-mount-opts',
    'install-same', 'parent', 'readonly', 'region-size', 'region-start', 'resize', 'type',
}
GROUP_KEYS = {
    'keyring': {'check-purpose', 'directory', 'path', 'allow-partial-chain', 'use-bundle-signing-time',
                'check-crl', 'allowed-signer-cns'},
    'handlers': {'bootloader-custom-backend', 'post-install', 'pre-install', 'system-info'},
    'casync': {'install-args', 'storepath', 'tmppath', 'use-desync'},
    'streaming': {'sandbox-user', 'tls-ca', 'tls-cert', 'tls-key', 'send-headers'},
    'encryption': {'cert', 'key'},
    'autoinstall': {'path'},
    'log': set(),
    'artifacts': set(),
}


def check(path):
    cp = configparser.ConfigParser(interpolation=None, strict=True)
    cp.optionxform = str
    cp.read(path)
    errors = []
    if 'system' not in cp:
        errors.append('no [system] group')
    for group in cp.sections():
        allowed = (SYSTEM_KEYS if group == 'system' else SLOT_KEYS if group.startswith('slot.')
                   else GROUP_KEYS.get(group.split('.')[0]))
        if allowed is None:
            errors.append(f'[{group}]: unknown group')
            continue
        for key in cp[group]:
            if allowed and key not in allowed:
                errors.append(f'[{group}] {key}: not a RAUC {RAUC_VERSION} key')
    return errors


def main():
    files = sorted([*ROOT.glob('os/board/*/*/rootfs-overlay/etc/rauc/system.conf'),
                    *ROOT.glob('os/board/*/*/protocol2-overlay/etc/rauc/system.conf')])
    bad = 0
    for f in files:
        for e in check(f):
            print(f'{f.relative_to(ROOT)}: {e}')
            bad += 1
    if not files:
        print('no RAUC system.conf found')
        return 1
    print(f'RAUC config OK: {len(files)} file(s)' if not bad else f'{bad} problem(s)')
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
