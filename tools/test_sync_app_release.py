#!/usr/bin/env python3
"""Self-tests for tools/sync_app_release.py (the app → OS bridge).

Runs the sync script against a temp copy of this repository and checks the
resulting manifests with the real validator, plus rejection of bad payloads.
"""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
SYNC = ROOT / 'tools' / 'sync_app_release.py'
VALIDATOR = ROOT / 'tools' / 'validate_contracts.py'


def payload(ref='dev', commit='c977b05eb45f6625619f24cc1c457d468297a0ce', app='a', bms='b', version='2.7.0', **over):
    recs = [
        {'service': 'epilykos', 'image': 'irunmole/epilykos', 'digest': 'sha256:' + app * 64,
         'tag': f'irunmole/epilykos:{ref}', 'commit': commit, 'ref': ref, 'version': version},
        {'service': 'epilykos-bms', 'image': 'irunmole/epilykos-bms', 'digest': 'sha256:' + bms * 64,
         'tag': f'irunmole/epilykos-bms:{ref}', 'commit': commit, 'ref': ref, 'version': version},
    ]
    p = {'ref': ref, 'commit': commit, 'records': recs}
    p.update(over)
    return p


def sync(root, p):
    return subprocess.run([sys.executable, str(SYNC), '--root', str(root), '--payload', json.dumps(p)],
                          capture_output=True, text=True)


def validate(root):
    return subprocess.run([sys.executable, str(VALIDATOR), '--root', str(root)], capture_output=True, text=True)


def ok(name):
    print(f'ok - {name}')


tmp = Path(tempfile.mkdtemp(prefix='sync-'))
try:
    (tmp / 'contracts').mkdir()
    shutil.copy(ROOT / 'contracts' / 'epilykos-os-contracts.yaml', tmp / 'contracts')

    r = sync(tmp, payload())
    assert r.returncode == 0, r.stderr
    dev = yaml.safe_load((tmp / 'manifests' / 'dev.yaml').read_text())
    assert dev['channel'] == 'dev' and dev['sequence'] == 1, dev
    assert dev['images']['epilykos']['digest'] == 'sha256:' + 'a' * 64
    assert dev['images']['bms-bridge']['digest'] == 'sha256:' + 'b' * 64
    assert dev['release'] == '2.7.0-dev+c977b05'
    assert validate(tmp).returncode == 0, validate(tmp).stderr
    ok('dev build -> manifests/dev.yaml sequence 1, validator passes')

    r = sync(tmp, payload())
    assert r.returncode == 0 and 'unchanged' in r.stdout, r.stdout + r.stderr
    assert len((tmp / 'release/digest-records/dev.json').read_text().splitlines()) == 2
    ok('same digests again -> unchanged, records not duplicated')

    r = sync(tmp, payload(app='c', commit='d' * 40))
    assert r.returncode == 0, r.stderr
    assert yaml.safe_load((tmp / 'manifests/dev.yaml').read_text())['sequence'] == 2
    ok('new dev build -> sequence 2')

    r = sync(tmp, payload(ref='main', app='e', bms='f', commit='e' * 40, version='2.8.0'))
    assert r.returncode == 0, r.stderr
    stable = yaml.safe_load((tmp / 'manifests/stable.yaml').read_text())
    assert stable == {'schema': 1, 'release': '2.8.0', 'channel': 'stable', 'sequence': 1,
                      'images': {'bms-bridge': {'digest': 'sha256:' + 'f' * 64, 'source': 'irunmole/epilykos-bms'},
                                 'epilykos': {'digest': 'sha256:' + 'e' * 64, 'source': 'irunmole/epilykos'}}}, stable
    v = validate(tmp)
    assert v.returncode == 0, v.stderr
    ok('main build -> manifests/stable.yaml, provenance check passes')

    # Hand-edit the stable manifest to a dev-built digest: the validator must catch it.
    stable['images']['epilykos']['digest'] = 'sha256:' + 'c' * 64
    (tmp / 'manifests/stable.yaml').write_text(yaml.safe_dump(stable))
    v = validate(tmp)
    assert v.returncode == 1 and 'no main-branch digest record' in v.stderr, v.stderr
    ok('stable manifest pointing at a dev build is rejected by make contracts')

    bad = {
        'tag instead of digest': payload(app='z'),  # 'z'*64 is not hex
        'unknown service': {**payload(), 'records': payload()['records'] + [{**payload()['records'][0], 'service': 'evil'}]},
        'missing bms-bridge': {**payload(), 'records': payload()['records'][:1]},
        'record ref mismatch': {**payload(), 'records': [{**payload()['records'][0], 'ref': 'main'}, payload()['records'][1]]},
        'feature branch': payload(ref='feature-x'),
        'shell in version': payload(version='1.0;rm -rf /'),
        'not json object': [],
    }
    before = (tmp / 'manifests/dev.yaml').read_text()
    for name, p in bad.items():
        r = sync(tmp, p)
        assert r.returncode == 1 and 'rejected' in r.stderr, f'{name}: {r.stdout}{r.stderr}'
    assert (tmp / 'manifests/dev.yaml').read_text() == before
    ok(f'{len(bad)} malformed payloads rejected without writing')
finally:
    shutil.rmtree(tmp)

print('\nPASS sync self-tests')
