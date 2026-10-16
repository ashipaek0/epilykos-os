#!/usr/bin/env python3
"""Which version an OS image build is. Git tags hold it: vMAJOR.MINOR,
two parts (v0.1, v0.2 … v1.0, v1.1). Run from a checkout with tags and
history (fetch-depth: 0).

    os_version.py main             a build of main
    os_version.py dev              a build run by hand on any other branch
    os_version.py release v1.0     a release created by hand on GitHub

Prints JSON {"version", "tag", "is_new", "previous"}:
  main     the newest tag when this commit is it, or nothing that goes into
           the image changed since (os/, manifests/stable.yaml): same
           version, is_new false. Otherwise the next minor, is_new true; the
           workflow tags it and makes the GitHub Release (with the SD card
           image) once the image has built and booted. The first is v0.1.
  dev      next minor as a pre-release: 0.3-dev+abc1234
  release  the tag you typed; the next build of main counts on from it
These numbers are for people. Appliances order releases by the manifest
sequence (C-UPDATE-004), never by this.
"""
import json
import re
import subprocess
import sys

TAG_RE = re.compile(r'^v(\d+)\.(\d+)$')
IMAGE_PATHS = ['os', 'manifests/stable.yaml']


def git(*args, check=True):
    return subprocess.run(['git', *args], capture_output=True, text=True, check=check).stdout.strip()


def parse(tag):
    m = TAG_RE.match(tag or '')
    return (int(m.group(1)), int(m.group(2))) if m else None


def latest_tag(tags):
    tags = [t for t in tags if parse(t)]
    return max(tags, key=parse) if tags else None


def bump(tag):
    major, minor = parse(tag)
    return f'{major}.{minor + 1}'


def decide(mode, arg=None, repo=None):
    latest = latest_tag(repo['tags']())
    if mode == 'release':
        if not parse(arg):
            raise ValueError(f'release tag must look like v1.0, not "{arg}"')
        return {'version': arg[1:], 'tag': arg, 'is_new': False, 'previous': latest}
    if mode == 'dev':
        nxt = bump(latest) if latest else '0.1'
        return {'version': f'{nxt}-dev+{repo["head"]()[:7]}', 'tag': None, 'is_new': False, 'previous': latest}
    if mode == 'main':
        if not latest:
            return {'version': '0.1', 'tag': 'v0.1', 'is_new': True, 'previous': None}
        if repo['tag_commit'](latest) == repo['head']() or not repo['changed_since'](latest):
            return {'version': latest[1:], 'tag': latest, 'is_new': False, 'previous': latest}
        nxt = bump(latest)
        return {'version': nxt, 'tag': f'v{nxt}', 'is_new': True, 'previous': latest}
    raise ValueError('usage: os_version.py main | dev | release <tag>')


GIT_REPO = {
    'tags': lambda: git('tag', '--list', 'v*').splitlines(),
    'head': lambda: git('rev-parse', 'HEAD'),
    'tag_commit': lambda t: git('rev-list', '-n', '1', t),
    'changed_since': lambda t: subprocess.run(['git', 'diff', '--quiet', t, 'HEAD', '--', *IMAGE_PATHS]).returncode != 0,
}

if __name__ == '__main__':
    try:
        print(json.dumps(decide(sys.argv[1] if len(sys.argv) > 1 else '', sys.argv[2] if len(sys.argv) > 2 else None, GIT_REPO)))
    except ValueError as e:
        sys.exit(str(e))
