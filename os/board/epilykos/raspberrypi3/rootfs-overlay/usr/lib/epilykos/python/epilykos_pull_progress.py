"""How far the app image download has got (Settings -> Updates shows a bar).

`podman pull --quiet` says nothing while it works, so the size and the
progress are worked out beside it. Both are best effort: if either cannot be
found the page shows a moving bar, and the update is not affected.

  total     what the pull has to fetch: the layers of this image for this
            board (from the registry, by digest) minus the leading layers the
            box already has from the running image. Layers are compared by
            their uncompressed digests (diff IDs), which the image's config
            lists in the same order as its layers.
  progress  bytes received on the network since the pull started
            (/proc/net/dev, every interface but loopback and container ones).

Nothing is written to disk: hostd keeps this in memory and adds it to the
update job when Settings asks.
"""
import json
import os
import platform
import re
import threading
import time
import urllib.parse

NET_DEV = os.environ.get('EPILYKOS_NET_DEV', '/proc/net/dev')
SKIP_IFACE = re.compile(r'^(lo|veth|cni|podman|docker|br-)')
ACCEPT = ', '.join((
    'application/vnd.oci.image.index.v1+json',
    'application/vnd.docker.distribution.manifest.list.v2+json',
    'application/vnd.oci.image.manifest.v1+json',
    'application/vnd.docker.distribution.manifest.v2+json',
))
MAX_JSON = 1024 * 1024
ARCH = {'aarch64': 'arm64', 'arm64': 'arm64', 'x86_64': 'amd64', 'armv7l': 'arm', 'armv6l': 'arm'}
DIGEST_RE = re.compile(r'^sha256:[0-9a-f]{64}$')


def net_rx_bytes(path=None):
    """Bytes received so far on the box's real network interfaces."""
    total = 0
    try:
        with open(path or NET_DEV) as f:
            for line in f:
                if ':' not in line:
                    continue
                name, rest = line.split(':', 1)
                if SKIP_IFACE.match(name.strip()):
                    continue
                fields = rest.split()
                if fields and fields[0].isdigit():
                    total += int(fields[0])
    except OSError:
        return 0
    return total


def registry_base(registry):
    return {'docker.io': 'https://registry-1.docker.io'}.get(registry, 'https://' + registry)


class Registry:
    """The few registry calls needed, over `get(url, headers) -> (status, headers, body)`
    (headers lower-cased, redirects not followed)."""

    def __init__(self, base, source, get):
        self.base, self.source, self.get = base.rstrip('/'), source, get
        self.token = None

    def _auth(self, challenge):
        m = re.match(r'(?i)\s*bearer\s+(.*)', challenge or '')
        if not m:
            raise ValueError('unsupported registry login')
        params = dict(re.findall(r'(\w+)="([^"]*)"', m.group(1)))
        if not params.get('realm', '').startswith(('https://', 'http://')):
            raise ValueError('no token service')
        query = {'scope': params.get('scope') or f'repository:{self.source}:pull'}
        if params.get('service'):
            query['service'] = params['service']
        status, _h, body = self.get(params['realm'] + ('&' if '?' in params['realm'] else '?') + urllib.parse.urlencode(query), {})
        if status != 200:
            raise ValueError(f'token service answered {status}')
        doc = json.loads(body[:MAX_JSON])
        self.token = doc.get('token') or doc.get('access_token')
        if not self.token:
            raise ValueError('no token')

    def _get(self, url, accept=None):
        for attempt in range(2):
            headers = {'Accept': accept} if accept else {}
            if self.token:
                headers['Authorization'] = 'Bearer ' + self.token
            status, resp, body = self.get(url, headers)
            if status == 401 and attempt == 0 and not self.token:
                self._auth(resp.get('www-authenticate'))
                continue
            return status, resp, body
        raise ValueError('registry login refused')

    def manifest(self, ref):
        status, resp, body = self._get(f'{self.base}/v2/{self.source}/manifests/{ref}', ACCEPT)
        if status != 200:
            raise ValueError(f'manifest {status}')
        return json.loads(body[:MAX_JSON])

    def blob_json(self, digest):
        url = f'{self.base}/v2/{self.source}/blobs/{digest}'
        status, resp, body = self._get(url)
        if status in (301, 302, 303, 307, 308) and resp.get('location'):
            # The storage behind the registry takes no registry token.
            status, resp, body = self.get(urllib.parse.urljoin(url, resp['location']), {})
        if status != 200:
            raise ValueError(f'blob {status}')
        return json.loads(body[:MAX_JSON])


def machine_arch():
    return ARCH.get(platform.machine(), platform.machine())


def common_prefix(a, b):
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    return n


def plan_total(registry, digest, arch, local_layer_lists):
    """Bytes the pull has to fetch, or None when it cannot be worked out."""
    if not DIGEST_RE.match(digest or ''):
        return None
    doc = registry.manifest(digest)
    if 'manifests' in doc:   # an index: take this board's image
        picks = [m for m in doc['manifests']
                 if (m.get('platform') or {}).get('os') == 'linux' and (m.get('platform') or {}).get('architecture') == arch
                 and 'attestation' not in str((m.get('annotations') or {}).get('vnd.docker.reference.type', ''))]
        if not picks or not DIGEST_RE.match(picks[0].get('digest', '')):
            return None
        doc = registry.manifest(picks[0]['digest'])
    layers = doc.get('layers') or []
    config = doc.get('config') or {}
    if not layers or not all(isinstance(l.get('size'), int) and l['size'] >= 0 for l in layers):
        return None
    skip = 0
    try:
        diff_ids = (registry.blob_json(config['digest']).get('rootfs') or {}).get('diff_ids') or []
        if len(diff_ids) == len(layers):
            skip = max([common_prefix(diff_ids, local) for local in local_layer_lists] or [0])
    except (ValueError, KeyError, OSError):
        skip = 0   # not knowing what is already here only makes the total larger
    size = sum(l['size'] for l in layers[skip:])
    size += config['size'] if isinstance(config.get('size'), int) else 0
    return size if size > 0 else None


class Progress:
    """Beside `podman pull`: works out the total in the background, and reads the
    network counter whenever progress is asked for (nothing polls)."""

    def __init__(self, plan, rx=net_rx_bytes, log=lambda m: None):
        self._plan, self._rx, self._log = plan, rx, log
        self._lock = threading.Lock()
        self._thread = None
        self._start_rx = 0
        self._total = None
        self._closed = False

    def start(self):
        self._start_rx = self._rx()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        try:
            total = self._plan()
        except Exception as e:  # noqa: BLE001 - any failure here is only a missing bar
            self._log(f'download size unknown ({type(e).__name__}: {e})')
            total = None
        with self._lock:
            self._total = None if self._closed else total

    def stop(self):
        """The download is over: report nothing more."""
        with self._lock:
            self._closed = True
            self._total = None

    def snapshot(self):
        """{'total': n, 'bytes': n} once the total is known, else {}."""
        with self._lock:
            total = self._total
        if not total:
            return {}
        return {'total': total, 'bytes': min(max(0, self._rx() - self._start_rx), total)}
