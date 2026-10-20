#!/usr/bin/env python3
"""SD-card sizing and timezone/clock interface without changing the test host."""
import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path
sys.dont_write_bytecode = True
root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root/'os/board/epilykos/raspberrypi3/rootfs-overlay/usr/lib/epilykos/python'))
import epilykos_system as s
with tempfile.TemporaryDirectory() as temp:
    d=Path(temp); s.DATA=d/'data';s.RUN=d/'run';s.SYS=d/'sys'
    assert s.timezone() == 'UTC'
    assert s.set_timezone('Africa/Lagos') == 'Africa/Lagos'
    assert s.timezone() == 'Africa/Lagos'
    for zone in ('../../etc/passwd','/etc/passwd','Africa/../UTC','no-such-zone',123,None):
        try: s.validate_zone(zone)
        except s.SystemError: pass
        else: raise AssertionError(zone)
    s.write_clock(); c=json.loads((s.RUN/'clock.json').read_text());assert c['timezone']=='Africa/Lagos' and isinstance(c['synchronized'],bool)
    for value in ('1700000000',True,None,1,float('nan')):
        try: s.set_clock(value)
        except s.SystemError: pass
        else: raise AssertionError(value)
    dev=d/'devices/mmcblk0';part=dev/'mmcblk0p4';part.mkdir(parents=True);(part/'partition').write_text('4')
    s.SYS.mkdir();(s.SYS/'mmcblk0p4').symlink_to(part)
    (dev/'size').write_text(str(16_000_000_000//512));assert s.storage('/dev/mmcblk0p4')['undersized']
    (dev/'size').write_text(str(31_900_000_000//512));assert not s.storage('/dev/mmcblk0p4')['undersized']
    assert s.storage('/dev/not-here') is None
print('PASS system state: timezone persistence/path validation, clock JSON, card backing device and marketed 32 GB tolerance')
