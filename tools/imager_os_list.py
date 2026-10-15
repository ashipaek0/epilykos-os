#!/usr/bin/env python3
"""Write a Raspberry Pi Imager OS list for an EpilykosOS image.

Imager only offers its settings dialog (Wi-Fi name, password, country) for
images that come from an OS list declaring an `init_format`; a file picked
with "Use custom" gets no settings. This list declares "systemd", so Imager
writes firstrun.sh to the BOOT partition, and EpilykosOS imports the Wi-Fi
settings from it on first boot (epilykos-wifi-import). Nothing in
firstrun.sh is executed.

    tools/imager_os_list.py --image sdcard.img --download sdcard.img.xz \\
        --url https://.../sdcard.img.xz > epilykos-imager.json
    rpi-imager --repo epilykos-imager.json        # or a URL to it

--image is the uncompressed image (for the size and SHA-256 Imager checks
after writing); --download is the file at --url (for the download size).
Prefer .xz: Imager reads .zip, .gz and .xz everywhere, .zst only since 1.7.
"""
import argparse
import datetime
import hashlib
import json
import os
import sys

DEFAULT_ICON = 'https://raw.githubusercontent.com/ashipaek0/epilykos-os/main/os/imager/icon.png'


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def os_list(image, download, url, name, description, release_date, icon):
    return {
        'os_list': [{
            'name': name,
            'description': description,
            'icon': icon,
            'url': url,
            'extract_size': os.path.getsize(image),
            'extract_sha256': sha256(image),
            'image_download_size': os.path.getsize(download),
            'release_date': release_date,
            'init_format': 'systemd',
            'devices': ['pi3'],
            'website': 'https://github.com/ashipaek0/epilykos-os',
        }]
    }


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    p.add_argument('--image', required=True, help='uncompressed sdcard.img')
    p.add_argument('--download', required=True, help='the compressed file served at --url')
    p.add_argument('--url', required=True)
    p.add_argument('--name', default='EpilykosOS (Raspberry Pi 3B)')
    p.add_argument('--description', default='Epilykos energy monitoring appliance')
    p.add_argument('--release-date', default=datetime.date.today().isoformat())
    p.add_argument('--icon', default=DEFAULT_ICON)
    a = p.parse_args(argv)
    json.dump(os_list(a.image, a.download, a.url, a.name, a.description, a.release_date, a.icon),
              sys.stdout, indent=2)
    print()
    return 0


if __name__ == '__main__':
    sys.exit(main())
