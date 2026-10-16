#!/usr/bin/env python3
"""Self-tests for OS image versions (tools/os_version.py): v0.1, v0.2 … on
main, a release made by hand on GitHub sets a new starting point, changes
outside the image keep the version, other tags (app-dev) are ignored, and
post-build.sh writes the version CI chose into image.env."""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'tools'))
from os_version import decide, latest_tag  # noqa: E402

assert latest_tag(['v0.9', 'v0.10', 'v0.2', 'app-dev', 'v1.0.0']) == 'v0.10', 'version order; three-part and other tags ignored'
assert latest_tag([]) is None

d = Path(tempfile.mkdtemp(prefix='epilykos-osver-'))


def git(*a):
    return subprocess.run(['git', *a], cwd=d, capture_output=True, text=True, check=True).stdout.strip()


def run(*a):
    return json.loads(subprocess.run([sys.executable, str(ROOT / 'tools/os_version.py'), *a], cwd=d,
                                     capture_output=True, text=True, check=True).stdout)


def commit(f, msg):
    (d / f).parent.mkdir(parents=True, exist_ok=True)
    with open(d / f, 'a') as fh:
        fh.write(msg + '\n')
    git('add', '-A')
    git('commit', '-qm', msg)


git('init', '-q')
git('config', 'user.email', 't@t')
git('config', 'user.name', 't')
commit('os/build.sh', 'first')
git('tag', 'app-dev')
assert run('main') == {'version': '0.1', 'tag': 'v0.1', 'is_new': True, 'previous': None}, 'the first OS version is 0.1'
git('tag', 'v0.1')
assert run('main')['is_new'] is False and run('main')['version'] == '0.1'
commit('contracts/x.yaml', 'contract text')
commit('manifests/dev.yaml', 'dev app')
assert run('main')['version'] == '0.1', 'changes outside the image keep the version'
commit('manifests/stable.yaml', 'stable app')
assert run('main') == {'version': '0.2', 'tag': 'v0.2', 'is_new': True, 'previous': 'v0.1'}, 'a new stable app is a new OS version'
assert run('dev')['version'].startswith('0.2-dev+')
git('tag', 'v0.2')
commit('os/x', 'big')
assert run('release', 'v1.0')['version'] == '1.0'
git('tag', 'v1.0')
commit('os/y', 'next')
assert run('main')['version'] == '1.1', 'counts on from the release you made'
for bad in ('v1', 'v1.0.0', '1.0'):
    try:
        decide('release', bad, {'tags': lambda: []})
        raise AssertionError(bad)
    except ValueError:
        pass
print('ok - v0.1, v0.2 … on main; a GitHub release sets the version; changes outside the image keep it')

pb = (ROOT / 'os/board/epilykos/raspberrypi3/post-build.sh').read_text()
assert 'OS_VERSION=${EPILYKOS_OS_VERSION:-' in pb, 'post-build takes the version CI chose'
assert "--match 'v[0-9]*'" in pb, 'and otherwise describes from version tags only'
wf = (ROOT / '.github/workflows/os-build.yml').read_text()
assert 'tools/os_version.py' in wf and 'types: [published]' in wf and 'gh release' in wf
print('ok - the image build writes the version into image.env and releases it')
print('\nPASS OS version self-tests')
