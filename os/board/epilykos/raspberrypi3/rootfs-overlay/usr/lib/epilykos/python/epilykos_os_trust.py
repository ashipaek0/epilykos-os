"""Shared OS trust (OS update design §6, plan task 4).

One implementation for the image build (tools/os_bootstrap.py imports it)
and both slots on the box:

- the offline OS index root's signed trust policy, kept in the redundant
  U-Boot environment (epk_trust_policy/_sig/_epoch) and verified against
  the root public key pinned on BOOT;
- signed per-package authorization entries from a delegated index key the
  current policy lists (design §6.2);
- the RAUC signer allowlist (leaf SPKI) and agreement between a bundle's
  signed manifest ([meta.epilykos]) and its entry;
- the effective RAUC keyring generated from the verified policy.

Validation is fail-closed. CRC-valid environment state is not authenticity:
the policy signature is checked every time it is used. Native CMS chain and
signing-time checks stay with RAUC; this module adds what RAUC cannot know.
"""
import base64
import configparser
import hashlib
import json
import os
from pathlib import Path
import re
import struct
import subprocess
import tempfile

POLICY_MAX = 6144
ENTRY_MAX = 8192
UINT64_MAX = (1 << 64) - 1
ED25519_SPKI = bytes.fromhex("302a300506032b6570032100")
BUNDLE_MAX = 3 * 1024 ** 3
SLOT_BYTES = 1536 * 1024 * 1024
COMPATIBLE = "epilykos-raspberrypi3"
HOST_API = 2
OPENSSL = os.environ.get("EPILYKOS_OPENSSL", "openssl")
FW_PRINTENV = os.environ.get("EPILYKOS_FW_PRINTENV", "fw_printenv")
FW_SETENV = os.environ.get("EPILYKOS_FW_SETENV", "fw_setenv")
PLATFORM = os.environ.get("EPILYKOS_PLATFORM", "/usr/lib/epilykos/platform.json")
ROOT_PEM = os.environ.get("EPILYKOS_OS_ROOT", "/boot/os-index-root.pem")
KEYRING = os.environ.get("EPILYKOS_RAUC_KEYRING", "/run/rauc/epilykos-keyring.pem")
ENTRY_FIELDS = {"schema", "board", "channel", "sequence", "trust_epoch", "index_key_id", "version",
                "boot_protocol", "min_host_api", "bundle", "notes_url"}
META_FIELDS = ("board", "channel", "sequence", "boot_protocol", "min_host_api", "trust_epoch")


class TrustError(ValueError):
    """A sentence Settings can show; never a private path or key."""


# -- canonical JSON and bounded scalars ------------------------------------------

def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                      allow_nan=False).encode("ascii")


def strict_json(raw, maximum):
    if not isinstance(raw, bytes) or not 0 < len(raw) <= maximum:
        raise ValueError("JSON size limit")

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    try:
        value = json.loads(raw, object_pairs_hook=pairs)
        if canonical(value) != raw:
            raise ValueError("JSON must use canonical sorted ASCII encoding")
    except (UnicodeError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid JSON") from exc
    return value


def integer(value, minimum=0):
    if type(value) is not int or not minimum <= value <= UINT64_MAX:
        raise ValueError("invalid unsigned integer")
    return value


def text(value, maximum):
    if not isinstance(value, str) or not 0 < len(value) <= maximum or not all(32 <= ord(c) <= 126 for c in value):
        raise ValueError("invalid bounded printable string")
    return value


def b64(value, size):
    try:
        raw = base64.b64decode(value, validate=True)
    except (ValueError, TypeError) as exc:
        raise ValueError("invalid base64") from exc
    if len(raw) != size or base64.b64encode(raw).decode() != value:
        raise ValueError("invalid canonical base64 length")
    return raw


def openssl(*args):
    try:
        result = subprocess.run([OPENSSL, *map(str, args)], capture_output=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError("OpenSSL unavailable or timed out") from exc
    if result.returncode:
        raise ValueError("OpenSSL rejected public material or signature")
    return result.stdout


def ed25519_verify(public_pem, data, signature):
    if not isinstance(signature, bytes) or len(signature) != 64:
        raise ValueError("Ed25519 signature must contain exactly 64 bytes")
    with tempfile.TemporaryDirectory(prefix="epk-verify-") as directory:
        path = Path(directory)
        (path / "key.pem").write_bytes(public_pem)
        der = openssl("pkey", "-pubin", "-in", path / "key.pem", "-outform", "DER")
        if len(der) != 44 or not der.startswith(ED25519_SPKI):
            raise ValueError("key must be Ed25519")
        (path / "data").write_bytes(data)
        (path / "signature").write_bytes(signature)
        openssl("pkeyutl", "-verify", "-rawin", "-pubin", "-inkey", path / "key.pem",
                "-in", path / "data", "-sigfile", path / "signature")


def raw_public_pem(raw32):
    der = ED25519_SPKI + raw32
    return (b"-----BEGIN PUBLIC KEY-----\n" + base64.encodebytes(der).replace(b"\n", b"") +
            b"\n-----END PUBLIC KEY-----\n")


# -- the root-signed policy ------------------------------------------------------------

def verify_policy(raw, signature, root_pubkey, *, board, channel, current=None):
    """Verify exact root-authorized bytes and schema, optionally monotonic to current.

    `root_pubkey` must come from the fixed trusted BOOT/build input, never from
    the policy itself. `current`, when supplied, is previously verified state
    with its exact `raw` bytes and scalar `trust_epoch` (not untrusted JSON).
    """
    policy = strict_json(raw, POLICY_MAX)
    fields = {"schema", "board", "channel", "trust_epoch", "index_keys", "rauc_cas", "rauc_leaf_spki_sha256"}
    if type(policy) is not dict or set(policy) != fields or type(policy["schema"]) is not int or policy["schema"] != 1:
        raise ValueError("unsupported policy schema")
    if board != "raspberrypi3" or channel not in ("dev", "stable") or policy["board"] != board or policy["channel"] != channel:
        raise ValueError("policy board/channel mismatch")
    integer(policy["trust_epoch"], 1)
    if not isinstance(signature, bytes) or len(signature) != 64:
        raise ValueError("Ed25519 signature must contain exactly 64 bytes")
    if not isinstance(root_pubkey, bytes) or not 1 <= len(root_pubkey) <= 256 or not re.fullmatch(rb"-----BEGIN PUBLIC KEY-----\n[A-Za-z0-9+/=\n]+\n-----END PUBLIC KEY-----\n", root_pubkey):
        raise ValueError("expected pinned public PEM key")
    with tempfile.TemporaryDirectory(prefix="epk-policy-") as directory:
        path = Path(directory)
        (path / "root.pem").write_bytes(root_pubkey)
        der = openssl("pkey", "-pubin", "-in", path / "root.pem", "-outform", "DER")
        if len(der) != 44 or not der.startswith(ED25519_SPKI):
            raise ValueError("offline root must be Ed25519")
        (path / "policy").write_bytes(raw)
        (path / "signature").write_bytes(signature)
        openssl("pkeyutl", "-verify", "-rawin", "-pubin", "-inkey", path / "root.pem",
                "-in", path / "policy", "-sigfile", path / "signature")
        keys = policy["index_keys"]
        if type(keys) is not list or not 1 <= len(keys) <= 8:
            raise ValueError("index key count")
        ids, public_keys = set(), set()
        for key in keys:
            if type(key) is not dict or set(key) != {"key_id", "public_key"}:
                raise ValueError("index key schema")
            if not isinstance(key["key_id"], str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", key["key_id"]):
                raise ValueError("index key identifier")
            public = b64(key["public_key"], 32)
            if key["key_id"] in ids or public in public_keys:
                raise ValueError("duplicate delegated key")
            ids.add(key["key_id"])
            public_keys.add(public)
        cas = policy["rauc_cas"]
        if type(cas) is not list or not 1 <= len(cas) <= 4 or any(type(c) is not str for c in cas) or len(set(cas)) != len(cas):
            raise ValueError("RAUC CA count/duplicates")
        for certificate in cas:
            # One explicit CA per entry; never silently parse the first of a chain.
            if certificate.count("-----BEGIN CERTIFICATE-----") != 1 or certificate.count("-----END CERTIFICATE-----") != 1:
                raise ValueError("expected one CA PEM certificate")
            if not re.fullmatch(r"-----BEGIN CERTIFICATE-----\n[A-Za-z0-9+/=\n]+\n-----END CERTIFICATE-----\n", certificate):
                raise ValueError("invalid certificate PEM")
            (path / "ca.pem").write_text(certificate, encoding="ascii")
            constraints = openssl("x509", "-in", path / "ca.pem", "-noout", "-ext", "basicConstraints")
            if b"CA:TRUE" not in constraints:
                raise ValueError("RAUC trust anchor is not a CA")
    leaves = policy["rauc_leaf_spki_sha256"]
    if type(leaves) is not list or not 1 <= len(leaves) <= 16 or any(type(v) is not str or not re.fullmatch("[0-9a-f]{64}", v) for v in leaves) or len(set(leaves)) != len(leaves):
        raise ValueError("RAUC leaf SPKI allowlist")
    if current is not None:
        epoch = integer(current["trust_epoch"], 1)
        if policy["trust_epoch"] < epoch or (policy["trust_epoch"] == epoch and raw != current["raw"]):
            raise ValueError("policy rollback or same-epoch mutation")
    return policy


# -- the platform this slot was built as -------------------------------------------------

def validate_platform(platform):
    fields = {"schema", "board", "arch", "slot_layout", "boot_protocol", "channel", "os_version",
              "os_sequence", "source", "kernel", "host_api", "app_release"}
    if type(platform) is not dict or set(platform) != fields:
        raise ValueError("platform schema fields")
    for key, expected in [("schema", 1), ("board", "raspberrypi3"), ("arch", "arm64"),
                          ("slot_layout", "pi3-mbr-v1"), ("boot_protocol", 2)]:
        if type(platform[key]) is not type(expected) or platform[key] != expected:
            raise ValueError(f"unsupported platform {key}")
    if platform["channel"] not in ("dev", "stable"):
        raise ValueError("invalid channel")
    integer(platform["os_sequence"], 1)
    integer(platform["host_api"], 2)
    for key, maximum in [("os_version", 64), ("kernel", 128), ("app_release", 64)]:
        text(platform[key], maximum)
    if not isinstance(platform["source"], str) or not re.fullmatch("[0-9a-f]{40}", platform["source"]):
        raise ValueError("source must be a full Git commit")
    return platform


# -- signed per-package authorization (design §6.2) ---------------------------------------

def verify_os_release(entry_bytes, signature, platform, policy):
    """The exact signed entry for one package, checked against this slot and
    the current verified policy. Returns the entry. Does not decide ordering."""
    try:
        return _verify_os_release(entry_bytes, signature, platform, policy)
    except TrustError:
        raise
    except ValueError as exc:
        raise TrustError("OS release authorization is malformed") from exc


def _verify_os_release(entry_bytes, signature, platform, policy):
    entry = strict_json(entry_bytes, ENTRY_MAX)
    if type(entry) is not dict or set(entry) != ENTRY_FIELDS or entry["schema"] != 1 or type(entry["schema"]) is not int:
        raise TrustError("unsupported OS release entry")
    if entry["board"] != platform["board"] or entry["channel"] != platform["channel"]:
        raise TrustError("OS release is for another board or channel")
    if entry["board"] != policy["board"] or entry["channel"] != policy["channel"]:
        raise TrustError("OS release does not match the trust policy")
    integer(entry["sequence"], 1)
    if type(entry["boot_protocol"]) is not int or entry["boot_protocol"] != 2:
        raise TrustError("OS release needs a different boot protocol")
    if integer(entry["min_host_api"], 1) > platform["host_api"]:
        raise TrustError("OS release needs a newer host")
    if integer(entry["trust_epoch"], 1) != policy["trust_epoch"]:
        raise TrustError("OS release was authorized under a different trust epoch")
    text(entry["version"], 64)
    notes = text(entry["notes_url"], 512)
    bundle = entry["bundle"]
    if type(bundle) is not dict or set(bundle) != {"url", "size", "sha256"}:
        raise TrustError("OS release bundle schema")
    url = text(bundle["url"], 512)
    if not url.startswith("https://") or not notes.startswith("https://"):
        raise TrustError("OS release links must use HTTPS")
    if not 1 <= integer(bundle["size"], 1) <= BUNDLE_MAX:
        raise TrustError("OS release bundle size")
    if not isinstance(bundle["sha256"], str) or not re.fullmatch("[0-9a-f]{64}", bundle["sha256"]):
        raise TrustError("OS release bundle hash")
    keys = {key["key_id"]: key["public_key"] for key in policy["index_keys"]}
    if not isinstance(entry["index_key_id"], str) or entry["index_key_id"] not in keys:
        raise TrustError("OS release signed by a key the trust policy does not list")
    try:
        ed25519_verify(raw_public_pem(b64(keys[entry["index_key_id"]], 32)), entry_bytes, signature)
    except ValueError as exc:
        raise TrustError("OS release signature does not verify") from exc
    return entry


def admissible(entry, platform, floor):
    """Offered only above both the confirmed floor and this slot's own sequence."""
    if entry["sequence"] <= floor or entry["sequence"] <= platform["os_sequence"]:
        raise TrustError("OS release is not newer than this box's system")
    return entry


# -- bundles: signer allowlist and signed manifest agreement -------------------------------

def bundle_signature(path):
    """The CMS signature RAUC appended to a bundle: [payload][CMS DER][u64 BE size]."""
    path = Path(path)
    size = path.stat().st_size
    if size < 64:
        raise TrustError("bundle signature is missing or malformed")
    with path.open("rb") as stream:
        stream.seek(size - 8)
        length, = struct.unpack(">Q", stream.read(8))
        if not 0 < length <= 64 * 1024 or length + 8 >= size:
            raise TrustError("bundle signature is missing or malformed")
        stream.seek(size - 8 - length)
        return stream.read(length)


def bundle_signer(path, keyring):
    """Verify the bundle's CMS against `keyring` (signature and chain, any
    time; RAUC enforces signing time), returning (manifest text, signer SPKI
    SHA256). Only verity bundles carry their manifest inside the CMS."""
    with tempfile.TemporaryDirectory(prefix="epk-bundle-") as directory:
        work = Path(directory)
        (work / "sig.der").write_bytes(bundle_signature(path))
        try:
            openssl("cms", "-verify", "-inform", "DER", "-binary", "-in", work / "sig.der", "-CAfile", keyring,
                    "-no_check_time", "-signer", work / "signer.pem", "-out", work / "manifest")
        except ValueError as exc:
            raise TrustError("bundle signature does not verify against the trust policy's CAs") from exc
        public = openssl("x509", "-in", work / "signer.pem", "-pubkey", "-noout")
        (work / "signer-key.pem").write_bytes(public)
        spki = openssl("pkey", "-pubin", "-outform", "DER", "-in", work / "signer-key.pem")
        manifest = (work / "manifest").read_bytes()
    if len(manifest) > 64 * 1024:
        raise TrustError("bundle manifest too large")
    return manifest.decode("ascii", "strict"), hashlib.sha256(spki).hexdigest()


def check_manifest(manifest, entry):
    """The bundle's own signed manifest must say what the entry says, carry
    exactly one rootfs image and no hooks or custom handlers."""
    parser = configparser.ConfigParser(interpolation=None, strict=True)
    parser.optionxform = str
    try:
        parser.read_string(manifest)
    except configparser.Error as exc:
        raise TrustError("bundle manifest is malformed") from exc
    if parser.get("update", "compatible", fallback="") != COMPATIBLE:
        raise TrustError("bundle is for another system")
    if parser.get("bundle", "format", fallback="") != "verity":
        raise TrustError("only verity bundles are accepted")
    images = [s for s in parser.sections() if s.startswith("image.")]
    if images != ["image.rootfs"] or "hooks" in parser or any(
            key in parser["image.rootfs"] for key in ("hooks", "adaptive")):
        raise TrustError("bundle must carry exactly one rootfs image and no hooks")
    if "handler" in parser:
        raise TrustError("bundle must not carry a custom handler")
    meta = parser["meta.epilykos"] if "meta.epilykos" in parser else {}
    for key in META_FIELDS:
        if str(meta.get(key, "")) != str(entry[key]):
            raise TrustError(f"bundle and its authorization disagree on {key}")
    return parser


def file_identity(path):
    """(size, sha256) of a file, read once in bounded chunks."""
    digest, size = hashlib.sha256(), 0
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
            size += len(chunk)
    return size, digest.hexdigest()


def check_bundle(path, entry, policy, keyring):
    """Everything about a downloaded package a box checks before RAUC writes
    a slot: the exact authorized bytes (payload tampering included; dm-verity
    checks it again while installing), the actual CMS signer and its signed
    manifest. RAUC then repeats its own native verification."""
    if file_identity(path) != (entry["bundle"]["size"], entry["bundle"]["sha256"]):
        raise TrustError("downloaded package differs from its authorization")
    manifest, spki = bundle_signer(path, keyring)
    if spki not in policy["rauc_leaf_spki_sha256"]:
        raise TrustError("bundle signed by a signer the trust policy does not list")
    check_manifest(manifest, entry)
    return {"signer_spki_sha256": spki}


def keyring_pem(policy):
    return "".join(policy["rauc_cas"]).encode("ascii")


# -- the shared state in the redundant environment -------------------------------------------

def read_env():
    try:
        result = subprocess.run([FW_PRINTENV], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        raise TrustError("system state is unreadable; OS updates are off") from exc
    if result.returncode:
        raise TrustError("system state is unreadable; OS updates are off")
    values = {}
    for line in result.stdout.splitlines():
        name, sep, value = line.partition("=")
        if sep:
            values[name] = value
    return values


def current_state(platform, env=None, root_pem=None):
    """The verified current trust policy and floor. Missing, malformed or
    unverifiable state disables OS installation; it is never reset to zero."""
    env = read_env() if env is None else env
    try:
        root = Path(ROOT_PEM).read_bytes() if root_pem is None else root_pem
        raw = base64.b64decode(env["epk_trust_policy"], validate=True)
        signature = base64.b64decode(env["epk_trust_policy_sig"], validate=True)
        epoch, floor = env["epk_trust_epoch"], env["epk_os_floor"]
        if env.get("epk_state_schema") != "1" or not epoch.isdigit() or not floor.isdigit():
            raise ValueError("state schema")
        policy = verify_policy(raw, signature, root, board=platform["board"], channel=platform["channel"])
        if str(policy["trust_epoch"]) != epoch:
            raise ValueError("scalar epoch differs from the signed policy")
    except (KeyError, OSError, ValueError) as exc:
        raise TrustError("system trust state is missing or invalid; OS updates are off") from exc
    return {"policy": policy, "raw": raw, "signature": signature, "trust_epoch": policy["trust_epoch"],
            "floor": int(floor)}


def admit_policy(raw, signature, platform, state, root_pem=None):
    """Persist a newer root-signed policy (revocation delivery) before any
    install that needs it. Same epoch must be byte-identical; lower epochs
    are refused. One batch through the guarded fw_setenv, which re-checks
    monotonicity under its lock, advances the critical generation only on a
    change, and always writes both copies. So the same policy is written
    again rather than skipped: that completes the replication of a write
    an earlier cut left in one copy before it is acknowledged."""
    root = Path(ROOT_PEM).read_bytes() if root_pem is None else root_pem
    try:
        policy = verify_policy(raw, signature, root, board=platform["board"], channel=platform["channel"],
                               current={"raw": state["raw"], "trust_epoch": state["trust_epoch"]})
    except ValueError as exc:
        raise TrustError("the new trust policy is not acceptable") from exc
    script = (f"epk_trust_policy {base64.b64encode(raw).decode()}\n"
              f"epk_trust_policy_sig {base64.b64encode(signature).decode()}\n"
              f"epk_trust_epoch {policy['trust_epoch']}\n")
    try:
        result = subprocess.run([FW_SETENV, "--script", "-"], input=script, capture_output=True, text=True,
                                timeout=60)
    except (OSError, subprocess.SubprocessError) as exc:
        raise TrustError("could not save the new trust policy") from exc
    if result.returncode:
        raise TrustError("could not save the new trust policy")
    after = current_state(platform, root_pem=root)
    if after["raw"] != raw or after["trust_epoch"] != policy["trust_epoch"]:
        raise TrustError("the saved trust policy did not read back")
    return after


def write_keyring(policy, path=KEYRING):
    """The effective RAUC keyring: exactly the verified policy's CAs."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".new")
    temporary.write_bytes(keyring_pem(policy))
    os.chmod(temporary, 0o644)
    os.replace(temporary, path)
    return path


def load_platform(path=None):
    return validate_platform(strict_json(Path(path or PLATFORM).read_bytes()[:4097], 4096))


def keyring_main(path=KEYRING):
    """ExecStartPre of rauc.service. Exit 0 with a keyring, 1 without one."""
    try:
        state = current_state(load_platform())
        write_keyring(state["policy"], path)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
        print(f"epilykos-os-keyring: {exc}; RAUC will refuse every bundle", flush=True)
        return 1
    print(f"epilykos-os-keyring: trust epoch {state['trust_epoch']}, "
          f"{len(state['policy']['rauc_cas'])} CA(s)", flush=True)
    return 0
