#!/usr/bin/env python3
"""DATA's start-up ordering (first boot, C-BOOT-003).

Growing the partition (and writing a new filesystem on a factory reset)
makes udev announce the DATA device again, and for a moment its device unit
goes away. systemd-fsck@ is bound to that device and data.mount requires
it, so anything that does this after the check has run stops the check and
cancels the queued mount: /data stays unmounted and nothing that needs it
starts. The units that rewrite DATA must therefore finish before the check
starts. This reads the shipped unit files and holds that order.
"""
from pathlib import Path
import re
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent.parent
OVERLAY = ROOT / 'os/board/epilykos/raspberrypi3/rootfs-overlay'
UNITS = OVERLAY / 'usr/lib/systemd/system'
DEVICE = r'dev-disk-by\x2dpartuuid-45504c4b\x2d04.device'
FSCK = r'systemd-fsck@dev-disk-by\x2dpartuuid-45504c4b\x2d04.service'
MOUNT = 'data.mount'
REWRITERS = ('epilykos-data-grow.service', 'epilykos-factory-reset.service')


def ok(name):
    print(f'ok - {name}')


def ordering(name):
    """(after, before) unit names from a unit file's [Unit] section."""
    after, before = set(), set()
    for line in (UNITS / name).read_text().splitlines():
        match = re.match(r'(After|Before)=(.*)', line)
        if match:
            (after if match.group(1) == 'After' else before).update(match.group(2).split())
    return after, before


# Edges "a runs before b". The check follows its device; the mount follows
# the check (data.mount requires it and is ordered after it by systemd).
edges = {(DEVICE, FSCK), (FSCK, MOUNT)}
for name in REWRITERS:
    after, before = ordering(name)
    edges.update((a, name) for a in after)
    edges.update((name, b) for b in before)

for name in REWRITERS:
    after, before = ordering(name)
    assert FSCK in before, f'{name} must finish before the DATA file system check starts'
    assert FSCK not in after, f'{name} must not wait for the DATA file system check'
    assert MOUNT in before and DEVICE in after, f'{name} runs after the device and before the mount'
ok('the units that rewrite DATA run before its file system check and its mount')

after, _ = ordering('epilykos-factory-reset.service')
assert 'epilykos-data-grow.service' in after, 'a reset writes after the partition has been grown'
ok('a factory reset follows the grow')


def cyclic(graph):
    nodes = {n for e in graph for n in e}
    state = {}

    def visit(n):
        if state.get(n) == 1:
            return True
        if state.get(n) == 2:
            return False
        state[n] = 1
        if any(visit(b) for a, b in graph if a == n):
            return True
        state[n] = 2
        return False
    return any(visit(n) for n in nodes)


assert not cyclic(edges), 'the DATA start-up ordering loops'
ok('no ordering loop')

script = (OVERLAY / 'usr/bin/epilykos-data-grow').read_text()
assert script.index('partx -u') < script.index('udevadm settle') < script.index('systemctl is-active'), \
    'after the table changes: settle udev, then wait for systemd to see the device again'
ok('the grow waits for systemd to see the device again before the check relies on it')

print('\nPASS DATA unit ordering self-tests')
