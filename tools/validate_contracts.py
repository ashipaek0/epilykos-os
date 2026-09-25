#!/usr/bin/env python3
"""`make contracts` — validate the EpilykosOS contract registry (§8).

Always checked:
  - registry parses; required sections present
  - IDs unique across invariants, contracts, acceptance tests, decisions
  - contract status / stage values valid
  - decisions carry due_stage, status, stage0_review_required and only block
    contracts that exist; `unresolved` decision references exist
  - no stage listed in metadata.releasable_stages has an open decision due
    at or before it

Checked once the inputs exist (directories are optional until then):
  - quadlets/*.container: no Privileged=true without an
    `# exception: <id>` marker; the Epilykos unit sets LOG_TO_FILE=false and
    SQLITE_SYNCHRONOUS, and its HealthCmd calls /healthz
  - manifests/*.yaml: schema 1, channel stable|dev, integer sequence >= 1,
    sha256 digests for epilykos and bms-bridge, no mutable tags; stable
    manifests only reference digests with a `main` record in
    release/digest-records/*.json

Exit status 0 = valid, 1 = violations (all are printed).
"""
import json
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
if '--root' in sys.argv:  # used by the self-tests to point at fixtures
    ROOT = Path(sys.argv[sys.argv.index('--root') + 1]).resolve()
REGISTRY = ROOT / 'contracts' / 'epilykos-os-contracts.yaml'
STATUSES = {'draft', 'decision-required', 'blocked-on-spike', 'deferred-v1', 'passed'}
DECISION_STATUSES = {'open', 'resolved'}
DIGEST = re.compile(r'^sha256:[0-9a-f]{64}$')
DECISION_REF = re.compile(r'\bD-[A-Z]+-\d{3}\b')

errors = []


def err(msg):
    errors.append(msg)


def check_registry(reg):
    for section in ('metadata', 'frozen_invariants', 'contracts', 'acceptance_tests', 'decisions_required'):
        if section not in reg:
            err(f'registry: missing section {section}')
    ids = {}
    for section in ('frozen_invariants', 'contracts', 'acceptance_tests', 'decisions_required'):
        for item in reg.get(section, []) or []:
            iid = item.get('id')
            if not iid:
                err(f'{section}: entry without id')
                continue
            if iid in ids:
                err(f'duplicate id {iid} (in {section} and {ids[iid]})')
            ids[iid] = section

    contract_ids = {c['id'] for c in reg.get('contracts', [])}
    decision_ids = {d['id'] for d in reg.get('decisions_required', [])}

    for c in reg.get('contracts', []):
        if c.get('status') not in STATUSES:
            err(f"{c['id']}: invalid status {c.get('status')!r}")
        if not isinstance(c.get('stage'), int) or not 0 <= c['stage'] <= 6:
            err(f"{c['id']}: stage must be an integer 0-6")
        if not c.get('requirements'):
            err(f"{c['id']}: no requirements")
        for u in c.get('unresolved', []) or []:
            for ref in DECISION_REF.findall(str(u)):
                if ref not in decision_ids:
                    err(f"{c['id']}: unresolved references unknown decision {ref}")

    for t in reg.get('acceptance_tests', []):
        if not isinstance(t.get('stage'), int):
            err(f"{t['id']}: stage must be an integer")

    for d in reg.get('decisions_required', []):
        for key in ('due_stage', 'status', 'stage0_review_required', 'blocks', 'question'):
            if key not in d:
                err(f"{d['id']}: missing {key}")
        if d.get('status') not in DECISION_STATUSES:
            err(f"{d['id']}: invalid status {d.get('status')!r}")
        if d.get('stage0_review_required') is not True:
            err(f"{d['id']}: must be visible to the Stage 0 review")
        for b in d.get('blocks', []) or []:
            if b not in contract_ids:
                err(f"{d['id']}: blocks unknown contract {b}")

    ordered = [(d['due_stage'], d['id']) for d in reg.get('decisions_required', [])]
    if ordered != sorted(ordered):
        err('decisions_required must be ordered by due_stage, then id')

    for stage in reg.get('metadata', {}).get('releasable_stages', []) or []:
        blocking = [d['id'] for d in reg.get('decisions_required', [])
                    if d.get('status') == 'open' and d.get('due_stage', 99) <= stage]
        if blocking:
            err(f'stage {stage} is marked releasable but has open decisions: {", ".join(blocking)}')


def check_quadlets():
    qdir = ROOT / 'quadlets'
    if not qdir.is_dir():
        return
    for unit in sorted(qdir.glob('*.container')):
        text = unit.read_text()
        if re.search(r'^\s*Privileged\s*=\s*true', text, re.M | re.I) and not re.search(r'#\s*exception:\s*\S+', text):
            err(f'{unit.name}: Privileged=true without an approved `# exception: <id>` marker (I-006)')
        if unit.stem == 'epilykos':
            if not re.search(r'^\s*Environment\s*=\s*LOG_TO_FILE=false\b', text, re.M):
                err(f'{unit.name}: must set Environment=LOG_TO_FILE=false (C-RUNTIME-002)')
            if not re.search(r'^\s*Environment\s*=\s*SQLITE_SYNCHRONOUS=(NORMAL|FULL|EXTRA)\b', text, re.M):
                err(f'{unit.name}: must set SQLITE_SYNCHRONOUS explicitly (C-DATA-003)')
            if not re.search(r'^\s*HealthCmd\s*=.*?/healthz', text, re.M):
                err(f'{unit.name}: HealthCmd must call /healthz (C-RUNTIME-002)')
            if re.search(r'^\s*Image\s*=.*:(latest|dev)\s*$', text, re.M):
                err(f'{unit.name}: Image must be a digest supplied by the active manifest, not a mutable tag')


def load_digest_records():
    records = {}
    rdir = ROOT / 'release' / 'digest-records'
    if rdir.is_dir():
        for f in rdir.glob('*.json'):
            for line in f.read_text().splitlines():
                if line.strip():
                    r = json.loads(line)
                    records.setdefault(r['digest'], set()).add(r.get('ref'))
    return records


def check_manifests():
    mdir = ROOT / 'manifests'
    if not mdir.is_dir():
        return
    records = load_digest_records()
    for path in sorted(mdir.glob('*.y*ml')):
        m = yaml.safe_load(path.read_text()) or {}
        name = path.name
        if m.get('schema') != 1:
            err(f'{name}: schema must be 1')
        if m.get('channel') not in ('stable', 'dev'):
            err(f'{name}: channel must be stable or dev (C-UPDATE-002)')
        seq = m.get('sequence')
        if not isinstance(seq, int) or isinstance(seq, bool) or seq < 1:
            err(f'{name}: sequence must be an integer >= 1 (C-UPDATE-002)')
        images = m.get('images') or {}
        for svc in ('epilykos', 'bms-bridge'):
            img = images.get(svc) or {}
            digest = str(img.get('digest', ''))
            if not DIGEST.match(digest):
                err(f'{name}: images.{svc}.digest must be sha256:<64 hex>, not a tag')
            elif m.get('channel') == 'stable' and 'main' not in records.get(digest, set()):
                err(f'{name}: images.{svc} digest has no main-branch digest record (I-009)')


def main():
    try:
        reg = yaml.safe_load(REGISTRY.read_text())
    except (OSError, yaml.YAMLError) as e:
        print(f'registry invalid: {e}', file=sys.stderr)
        return 1
    check_registry(reg)
    check_quadlets()
    check_manifests()
    if errors:
        for e in errors:
            print(f'FAIL {e}', file=sys.stderr)
        print(f'\n{len(errors)} contract violation(s)', file=sys.stderr)
        return 1
    print(f"contracts OK: {len(reg['contracts'])} contracts, {len(reg['acceptance_tests'])} tests, "
          f"{len(reg['decisions_required'])} decisions "
          f"({sum(1 for d in reg['decisions_required'] if d['status'] == 'open')} open)")
    return 0


if __name__ == '__main__':
    sys.exit(main())
