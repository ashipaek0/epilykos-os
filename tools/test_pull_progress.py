#!/usr/bin/env python3
"""The app update's download progress (epilykos_pull_progress, wired into
epilykos_update): the total from the registry minus the layers the box already
has, the progress from the network counter, and the job hostd reports.
Everything runs against fakes; no registry is contacted.
"""
import hashlib
import http.server
import json
import os
import sys
import tempfile
import threading
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent.parent
PYLIB = ROOT / 'os/board/epilykos/raspberrypi3/rootfs-overlay/usr/lib/epilykos/python'
T = Path(tempfile.mkdtemp(prefix='epilykos-pull-'))
os.environ.update(EPILYKOS_UPDATE_DIR=str(T / 'updater'), EPILYKOS_IMAGE_ENV=str(T / 'image.env'),
                  EPILYKOS_NET_DEV=str(T / 'net-dev'), EPILYKOS_REGISTRY_URL='http://127.0.0.1:9')
sys.path.insert(0, str(PYLIB))
import epilykos_pull_progress as pp  # noqa: E402
import epilykos_update as up  # noqa: E402

failures = []


def check(cond, msg):
    if not cond:
        failures.append(msg)


def sha(label):
    return 'sha256:' + hashlib.sha256(label.encode()).hexdigest()


# -- the network counter ---------------------------------------------------------------
dev = T / 'net-dev'
dev.write_text('''Inter-|   Receive                                                |  Transmit
 face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets errs drop fifo colls carrier compressed
    lo: 9000000      10    0    0    0     0          0         0  9000000      10    0    0    0     0       0          0
  eth0: 1000      10    0    0    0     0          0         0     5000      10    0    0    0     0       0          0
 wlan0:  500       5    0    0    0     0          0         0     2000       5    0    0    0     0       0          0
 veth1: 77777       5    0    0    0     0          0         0       10       5    0    0    0     0       0          0
cni-podman0: 88888       5    0    0    0     0          0         0   10       5    0    0    0     0       0          0
''')
check(pp.net_rx_bytes() == 1500, f'only the real interfaces count: {pp.net_rx_bytes()}')
check(pp.net_rx_bytes(str(T / 'missing')) == 0, 'no counter file reads as zero')

# -- the registry: login, index, manifest, config, redirected blobs --------------------------
L = [sha(f'layer{i}') for i in range(4)]            # compressed layer digests
D = [sha(f'diff{i}') for i in range(4)]             # uncompressed (diff IDs)
SIZES = [30_000_000, 20_000_000, 8_000_000, 2_000_000]
CONFIG = sha('config')
MANIFEST = sha('manifest-arm64')
INDEX = sha('index')
docs = {
    INDEX: {'schemaVersion': 2, 'manifests': [
        {'digest': sha('manifest-amd64'), 'platform': {'os': 'linux', 'architecture': 'amd64'}},
        {'digest': sha('attestation'), 'platform': {'os': 'unknown', 'architecture': 'unknown'},
         'annotations': {'vnd.docker.reference.type': 'attestation-manifest'}},
        {'digest': MANIFEST, 'platform': {'os': 'linux', 'architecture': 'arm64', 'variant': 'v8'}}]},
    MANIFEST: {'schemaVersion': 2, 'config': {'digest': CONFIG, 'size': 7000},
               'layers': [{'digest': d, 'size': s} for d, s in zip(L, SIZES)]},
}
CONFIG_DOC = {'rootfs': {'type': 'layers', 'diff_ids': D}}
requests = []


class Registry(http.server.BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.0'

    def log_message(self, *a):
        pass

    def _send(self, code, body=b'', headers=None):
        self.send_response(code)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        requests.append((self.path, self.headers.get('Authorization')))
        port = self.server.server_address[1]
        if self.path.startswith('/token'):
            check('scope=repository%3Airunmole%2Fepilykos%3Apull' in self.path and 'service=test-registry' in self.path, f'token request: {self.path}')
            return self._send(200, json.dumps({'token': 'sekret'}).encode())
        if self.path.startswith('/storage/'):
            # The storage behind the registry takes no registry token.
            check(self.headers.get('Authorization') is None, 'the registry token is not sent to the storage')
            return self._send(200, json.dumps(CONFIG_DOC).encode())
        if self.headers.get('Authorization') != 'Bearer sekret':
            return self._send(401, b'', {'WWW-Authenticate': f'Bearer realm="http://127.0.0.1:{port}/token",service="test-registry"'})
        if self.path.startswith('/v2/irunmole/epilykos/manifests/'):
            doc = docs.get(self.path.rsplit('/', 1)[1])
            return self._send(200, json.dumps(doc).encode()) if doc else self._send(404)
        if self.path == f'/v2/irunmole/epilykos/blobs/{CONFIG}':
            return self._send(307, b'', {'Location': f'http://127.0.0.1:{port}/storage/config'})
        self._send(404)


server = http.server.HTTPServer(('127.0.0.1', 0), Registry)
threading.Thread(target=server.serve_forever, daemon=True).start()
base = f'http://127.0.0.1:{server.server_address[1]}'
reg = pp.Registry(base, 'irunmole/epilykos', up._http_get)

# Nothing local: everything but the config's small share is the layers.
total = pp.plan_total(reg, INDEX, 'arm64', [])
check(total == sum(SIZES) + 7000, f'nothing local: all layers and the config: {total}')
check(any(a == 'Bearer sekret' for _p, a in requests), 'it logged in with the token the registry asked for')
check(any(p == '/storage/config' for p, _a in requests), 'the config blob came from the storage the registry redirected to')
check(not any(sha('manifest-amd64').split(':')[1] in p for p, _a in requests), 'it asked for this board\'s manifest, not the other')
check(not any(sha('attestation').split(':')[1] in p for p, _a in requests), 'attestation manifests are not images')

# The running image shares the first two layers: those are not downloaded.
reg2 = pp.Registry(base, 'irunmole/epilykos', up._http_get)
total = pp.plan_total(reg2, INDEX, 'arm64', [D[:2] + [sha('old3')], [sha('other')]])
check(total == SIZES[2] + SIZES[3] + 7000, f'two shared layers are left out: {total}')
# Shared layers must be a prefix: the same layer higher up the stack is a different layer.
reg3 = pp.Registry(base, 'irunmole/epilykos', up._http_get)
total = pp.plan_total(reg3, INDEX, 'arm64', [[sha('x'), D[1], D[2]]])
check(total == sum(SIZES) + 7000, f'a layer on a different base is downloaded: {total}')
# A manifest (not an index) works too; a bad digest is refused before any request.
reg4 = pp.Registry(base, 'irunmole/epilykos', up._http_get)
check(pp.plan_total(reg4, MANIFEST, 'arm64', []) == sum(SIZES) + 7000, 'a plain manifest works')
n = len(requests)
check(pp.plan_total(reg4, 'latest', 'arm64', []) is None and len(requests) == n, 'only a sha256 digest is asked for')
# No image for this board.
check(pp.plan_total(pp.Registry(base, 'irunmole/epilykos', up._http_get), INDEX, 's390x', []) is None, 'no image for this board: unknown')
# The registry says no.
try:
    pp.plan_total(pp.Registry(base, 'irunmole/epilykos', up._http_get), sha('nothing'), 'arm64', [])
    check(False, 'an unknown digest raises')
except ValueError:
    pass
server.shutdown()

# -- the watcher -----------------------------------------------------------------------------
rx = {'n': 1000}
watch = pp.Progress(lambda: 100, rx=lambda: rx['n'])
check(watch.snapshot() == {}, 'nothing before it starts')
watch.start()
watch._thread.join()
rx['n'] = 1040
check(watch.snapshot() == {'total': 100, 'bytes': 40}, f'bytes since the start: {watch.snapshot()}')
rx['n'] = 99999
check(watch.snapshot() == {'total': 100, 'bytes': 100}, 'never past the total')
rx['n'] = 500   # a counter that went backwards (interface reset)
check(watch.snapshot()['bytes'] == 0, 'never negative')
watch.stop()
check(watch.snapshot() == {}, 'nothing once the download is over')

logs = []
broken = pp.Progress(lambda: (_ for _ in ()).throw(OSError('no network')), rx=lambda: 0, log=logs.append)
broken.start(); broken._thread.join()
check(broken.snapshot() == {} and logs and 'download size unknown' in logs[0], f'a failed estimate is only a missing bar: {logs}')
unknown = pp.Progress(lambda: None, rx=lambda: 0)
unknown.start(); unknown._thread.join()
check(unknown.snapshot() == {}, 'an unknown total shows no numbers')

# -- what hostd reports ---------------------------------------------------------------------
os.makedirs(T / 'updater', exist_ok=True)
up._write_json('job.json', {'state': 'downloading', 'started_at': '2026-11-17T10:00:00Z', 'release': '1.2', 'sequence': 9, 'digest': sha('d')})
check('total' not in up.status()['app']['job'], 'no estimate yet: the job is as before')
rx['n'] = 0
w = pp.Progress(lambda: 5000, rx=lambda: rx['n']); w.start(); w._thread.join()
up._download = w
rx['n'] = 1250
job = up.status()['app']['job']
check(job['state'] == 'downloading' and job['total'] == 5000 and job['bytes'] == 1250, f'the job carries the progress: {job}')
check(json.loads((T / 'updater/job.json').read_text()).get('bytes') is None, 'nothing is written to disk for it')
up._write_json('job.json', {'state': 'starting', 'started_at': '2026-11-17T10:00:00Z', 'release': '1.2', 'sequence': 9, 'digest': sha('d')})
check('bytes' not in up.status()['app']['job'], 'only while it is downloading')
up._download = None
up._remove('job.json')
check(up.status()['app']['job'] is None, 'no job, nothing to add')

if failures:
    print('FAIL')
    for f in failures:
        print(' -', f)
    sys.exit(1)
print('ok: app update download progress')
