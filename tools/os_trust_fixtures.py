"""TEST-ONLY OS trust fixtures shared by the trust, release and bundle tests.

Real keys and certificates made with OpenSSL in a temporary directory:
an offline index root, delegated index keys, RAUC CAs and signing leaves,
root-signed policies at any epoch, signed entries and small ext4 rootfs
images carrying a protocol-2 platform.json.
"""
import base64
import hashlib
import os
from pathlib import Path
import subprocess
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] /
                       'os/board/epilykos/raspberrypi3/rootfs-overlay/usr/lib/epilykos/python'))
import epilykos_os_trust as trust  # noqa: E402

SOURCE = '0123456789abcdef0123456789abcdef01234567'


def openssl(*args, data=None):
    return subprocess.run(['openssl', *map(str, args)], input=data, check=True, capture_output=True).stdout


class Trust:
    """One test key custodian. Everything lives under `root`."""

    def __init__(self, root, channel='dev'):
        self.root, self.channel = Path(root), channel
        self.root.mkdir(parents=True, exist_ok=True)
        self.root_key = self.ed25519('os-index-root')
        self.root_pem = openssl('pkey', '-in', self.root_key, '-pubout')
        self.index = {}
        self.cas = {}
        self.leaves = {}

    def ed25519(self, name):
        path = self.root / f'{name}.key'
        openssl('genpkey', '-algorithm', 'ED25519', '-out', path)
        return path

    def index_key(self, key_id):
        path = self.ed25519(key_id)
        der = openssl('pkey', '-in', path, '-pubout', '-outform', 'DER')
        self.index[key_id] = (path, base64.b64encode(der[-32:]).decode())
        return key_id

    def ca(self, name):
        key, pem = self.root / f'{name}.key', self.root / f'{name}.pem'
        openssl('req', '-new', '-x509', '-newkey', 'ec', '-pkeyopt', 'ec_paramgen_curve:P-256', '-nodes',
                '-keyout', key, '-out', pem, '-days', 9131, '-subj', f'/CN=TEST-ONLY {name}',
                '-addext', 'basicConstraints=critical,CA:TRUE', '-addext', 'keyUsage=critical,keyCertSign,cRLSign')
        self.cas[name] = (key, pem)
        return name

    def leaf(self, name, ca):
        key, csr, pem = self.root / f'{name}.key', self.root / f'{name}.csr', self.root / f'{name}.pem'
        extensions = self.root / f'{name}.ext'
        extensions.write_text('basicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature\n')
        openssl('req', '-new', '-newkey', 'ec', '-pkeyopt', 'ec_paramgen_curve:P-256', '-nodes',
                '-keyout', key, '-out', csr, '-subj', f'/CN=TEST-ONLY {name}')
        ca_key, ca_pem = self.cas[ca]
        openssl('x509', '-req', '-in', csr, '-CA', ca_pem, '-CAkey', ca_key, '-CAcreateserial',
                '-CAserial', self.root / f'{ca}.srl', '-out', pem, '-days', 1826, '-extfile', extensions)
        spki = openssl('pkey', '-pubin', '-outform', 'DER', data=openssl('x509', '-in', pem, '-pubkey', '-noout'))
        self.leaves[name] = (key, pem, hashlib.sha256(spki).hexdigest())
        return name

    def policy(self, epoch, key_ids, ca_names, leaf_names, **override):
        value = {'schema': 1, 'board': 'raspberrypi3', 'channel': self.channel, 'trust_epoch': epoch,
                 'index_keys': [{'key_id': k, 'public_key': self.index[k][1]} for k in key_ids],
                 'rauc_cas': [self.cas[c][1].read_text() for c in ca_names],
                 'rauc_leaf_spki_sha256': [self.leaves[name][2] for name in leaf_names]}
        value.update(override)
        raw = trust.canonical(value)
        return raw, self.sign(self.root_key, raw)

    def sign(self, key, raw):
        path = self.root / 'to-sign'
        path.write_bytes(raw)
        return openssl('pkeyutl', '-sign', '-rawin', '-inkey', key, '-in', path)

    def entry(self, key_id, *, sequence=12, epoch=1, sign_with=None, raw=None, **override):
        value = {'schema': 1, 'board': 'raspberrypi3', 'channel': self.channel, 'sequence': sequence,
                 'trust_epoch': epoch, 'index_key_id': key_id, 'version': '0.7', 'boot_protocol': 2,
                 'min_host_api': 2,
                 'bundle': {'url': 'https://example.invalid/epilykos-os-0.7-raspberrypi3.raucb', 'size': 1234,
                            'sha256': 'a' * 64},
                 'notes_url': 'https://example.invalid/notes'}
        value.update(override)
        raw = trust.canonical(value) if raw is None else raw
        return raw, self.sign(self.index[sign_with or key_id][0], raw)


def platform(channel='dev', sequence=11, **override):
    value = {'schema': 1, 'board': 'raspberrypi3', 'arch': 'arm64', 'slot_layout': 'pi3-mbr-v1',
             'boot_protocol': 2, 'channel': channel, 'os_version': '0.6', 'os_sequence': sequence,
             'source': SOURCE, 'kernel': '6.6.31', 'host_api': 2, 'app_release': '1.0.4'}
    value.update(override)
    return value


def rootfs(path, platform_value, size_mib=8):
    """A small real ext4 image carrying /usr/lib/epilykos/platform.json."""
    path = Path(path)
    tree = path.with_suffix('.tree')
    (tree / 'usr/lib/epilykos').mkdir(parents=True)
    (tree / 'usr/lib/epilykos/platform.json').write_bytes(trust.canonical(platform_value))
    # Incompressible content: a squashfs payload must exceed one block.
    (tree / 'usr/lib/epilykos/filler').write_bytes(os.urandom(64 * 1024))
    subprocess.run(['mke2fs', '-q', '-F', '-t', 'ext4', '-d', str(tree), str(path), f'{size_mib}M'],
                   check=True, capture_output=True)
    return path


def env_for(raw, signature, epoch, floor=11, **override):
    """fw_printenv's view of the shared state for a policy."""
    values = {'epk_state_schema': '1', 'epk_state_generation': '4', 'epk_os_floor': str(floor),
              'epk_trust_epoch': str(epoch), 'epk_trust_policy': base64.b64encode(raw).decode(),
              'epk_trust_policy_sig': base64.b64encode(signature).decode()}
    values.update(override)
    return {k: v for k, v in values.items() if v is not None}
