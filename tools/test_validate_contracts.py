#!/usr/bin/env python3
"""Self-tests for tools/validate_contracts.py: every rule must actually fail.

Each case copies the real registry into a temp root, applies one breakage,
and asserts the validator exits 1 with the expected message. The unmodified
copy must pass.
"""
import copy
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
VALIDATOR = ROOT / 'tools' / 'validate_contracts.py'
REG = yaml.safe_load((ROOT / 'contracts' / 'epilykos-os-contracts.yaml').read_text())
GOOD_DIGEST = 'sha256:' + 'a' * 64
OTHER_DIGEST = 'sha256:' + 'b' * 64

GOOD_QUADLET = """[Container]
Image=registry.example/epilykos@%s
Environment=LOG_TO_FILE=false
Environment=SQLITE_SYNCHRONOUS=FULL
HealthCmd=node -e "fetch('http://127.0.0.1:3000/healthz')"
""" % GOOD_DIGEST


def manifest(channel='stable', digest=GOOD_DIGEST, seq=3):
    return {'schema': 1, 'release': '2.8.0', 'channel': channel, 'sequence': seq,
            'images': {'epilykos': {'digest': digest, 'source': 'docker.io/irunmole/epilykos'},
                       'bms-bridge': {'digest': digest, 'source': 'docker.io/irunmole/epilykos-bms'}}}


def run(case, mutate_registry=None, files=None, expect=None):
    tmp = Path(tempfile.mkdtemp(prefix='contracts-'))
    try:
        reg = copy.deepcopy(REG)
        if mutate_registry:
            mutate_registry(reg)
        (tmp / 'contracts').mkdir()
        (tmp / 'contracts' / 'epilykos-os-contracts.yaml').write_text(yaml.safe_dump(reg, sort_keys=False))
        for rel, content in (files or {}).items():
            path = tmp / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content if isinstance(content, str) else yaml.safe_dump(content))
        r = subprocess.run([sys.executable, str(VALIDATOR), '--root', str(tmp)], capture_output=True, text=True)
        out = r.stdout + r.stderr
        if expect is None:
            assert r.returncode == 0, f'{case}: expected pass, got:\n{out}'
        else:
            assert r.returncode == 1, f'{case}: expected failure, got exit {r.returncode}:\n{out}'
            assert expect in out, f'{case}: expected {expect!r} in:\n{out}'
        print(f'ok - {case}')
    finally:
        shutil.rmtree(tmp)


record = json.dumps({'service': 'epilykos', 'image': 'irunmole/epilykos', 'digest': GOOD_DIGEST,
                     'tag': 'irunmole/epilykos:latest', 'commit': 'abc', 'ref': 'main'})
dev_record = json.dumps({'service': 'epilykos', 'image': 'irunmole/epilykos', 'digest': OTHER_DIGEST,
                         'tag': 'irunmole/epilykos:dev', 'commit': 'def', 'ref': 'dev'})

run('unmodified registry passes')
run('valid quadlet + stable manifest with main record passes',
    files={'quadlets/epilykos.container': GOOD_QUADLET,
           'manifests/stable.yaml': manifest(),
           'release/digest-records/main.json': record + '\n'})
run('duplicate id', lambda r: r['acceptance_tests'].append(dict(r['acceptance_tests'][0])), expect='duplicate id')
run('invalid contract status', lambda r: r['contracts'][0].update(status='done'), expect='invalid status')
run('decision blocks unknown contract', lambda r: r['decisions_required'][0]['blocks'].append('C-NOPE-001'), expect='blocks unknown contract')
run('decision hidden from stage 0 review', lambda r: r['decisions_required'][0].update(stage0_review_required=False), expect='Stage 0 review')
run('unresolved names unknown decision', lambda r: r['contracts'][0].setdefault('unresolved', []).append('D-NOPE-999'), expect='unknown decision D-NOPE-999')
run('releasable stage with open decision', lambda r: r['metadata'].update(releasable_stages=[0]), expect='marked releasable')
run('privileged quadlet without exception', files={'quadlets/bms-bridge.container': '[Container]\nPrivileged=true\n'}, expect='Privileged=true')
run('epilykos quadlet without LOG_TO_FILE=false',
    files={'quadlets/epilykos.container': GOOD_QUADLET.replace('Environment=LOG_TO_FILE=false\n', '')}, expect='LOG_TO_FILE=false')
run('epilykos quadlet without /healthz',
    files={'quadlets/epilykos.container': GOOD_QUADLET.replace('/healthz', '/api/ping')}, expect='/healthz')
run('manifest with mutable tag', files={'manifests/m.yaml': manifest(digest='latest')}, expect='not a tag')
run('manifest without channel', files={'manifests/m.yaml': {**manifest(), 'channel': None}}, expect='channel must be')
run('manifest with string sequence', files={'manifests/m.yaml': manifest(seq='3')}, expect='sequence must be')
run('stable manifest pinning a dev-built digest',
    files={'manifests/m.yaml': manifest(digest=OTHER_DIGEST), 'release/digest-records/dev.json': dev_record + '\n'},
    expect='no main-branch digest record')
print('\nPASS validator self-tests')
