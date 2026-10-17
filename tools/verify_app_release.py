#!/usr/bin/env python3
"""Authenticate a dispatch against the source branch, successful build job and digest artifact.

Only GitHub's artifact from the expected app workflow can advance a manifest.
No payload-supplied URL, repository or image location is followed.
"""
import io
import json
import subprocess
import zipfile
from sync_app_release import validate, PayloadError

REPO = 'ashipaek0/Epilykos'


def api(path):
    return subprocess.check_output(['gh', 'api', f'repos/{REPO}/{path}'])


def verify(payload, fetch=api):
    ref, commit, records = validate(payload)
    head = json.loads(fetch(f'branches/{ref}'))['commit']['sha']
    if commit != head:
        raise PayloadError('stale release: source branch has advanced')
    filename = 'docker-build.yml' if ref == 'main' else 'docker-build-dev.yml'
    runs = json.loads(fetch(f'actions/workflows/{filename}/runs?head_sha={commit}&per_page=20'))['workflow_runs']
    for run in runs:
        if run.get('head_sha') != commit or run.get('head_branch') != ref or run.get('event') not in ('push', 'schedule', 'workflow_dispatch'):
            continue
        jobs = json.loads(fetch(f'actions/runs/{run["id"]}/jobs?per_page=100'))['jobs']
        builds = [j for j in jobs if j['name'].startswith('build-and-push')]
        if not builds or any(j.get('conclusion') != 'success' for j in builds):
            continue
        artifacts = json.loads(fetch(f'actions/runs/{run["id"]}/artifacts?per_page=100'))['artifacts']
        found = {}
        for a in artifacts:
            if a['name'] not in ('image-digest-epilykos', 'image-digest-epilykos-bms') or a.get('expired'):
                continue
            with zipfile.ZipFile(io.BytesIO(fetch(f'actions/artifacts/{a["id"]}/zip'))) as archive:
                for name in archive.namelist():
                    if name.startswith('digest-') and name.endswith('.json'):
                        info = archive.getinfo(name)
                        if info.file_size > 65536:
                            raise PayloadError('oversized digest record')
                        record = json.loads(archive.read(name))
                        found[record['service']] = record
        try:
            _, _, expected = validate({'ref': ref, 'commit': commit, 'records': list(found.values())})
        except PayloadError:
            continue
        if records == expected:
            return run['id']
    raise PayloadError('no successful source build with matching immutable digest artifacts')


if __name__ == '__main__':
    import sys
    try:
        print(verify(json.load(open(sys.argv[1]))))
    except (PayloadError, ValueError, KeyError, subprocess.CalledProcessError) as e:
        sys.exit(f'release rejected: {e}')
