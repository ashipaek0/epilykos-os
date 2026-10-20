#!/usr/bin/env python3
"""Validate raw OCI config from the actual preloaded archive; print its image ID."""
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'os/board/epilykos/raspberrypi3/rootfs-overlay/usr/lib/epilykos/python'))
from epilykos_app_compat import CompatibilityError, oci_config


def main():
    try:
        with open(sys.argv[1], 'rb') as stream:
            raw = stream.read(4 * 1024 * 1024 + 1)
        if len(raw) > 4 * 1024 * 1024:
            raise CompatibilityError('The app image configuration is too large')
        try:
            config = json.loads(raw)
        except (ValueError, UnicodeError, RecursionError):
            raise CompatibilityError('The app image configuration is unreadable') from None
        oci_config(config)
    except (CompatibilityError, OSError) as error:
        sys.exit(f'post-build: {error}')
    print(hashlib.sha256(raw).hexdigest())


if __name__ == '__main__':
    main()
