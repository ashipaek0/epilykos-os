#!/usr/bin/env python3
"""Host-only first-flash seed builder. Never opens a card or repairs live state.

Only public material is accepted. Policy signature verification uses OpenSSL's
Ed25519 implementation; native consumers separately enforce record durability.
This does not enable protocol 2 or implement native RAUC signer admission.
"""
import argparse
import base64
import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import tempfile
import zlib

SIZE = 16384
PAYLOAD = SIZE - 5
HEADROOM = 2048
POLICY_MAX = 6144
RUNTIME_RESERVE = 4096
UINT64_MAX = (1 << 64) - 1
ED25519_SPKI = bytes.fromhex("302a300506032b6570032100")


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
        result = subprocess.run(["openssl", *map(str, args)], capture_output=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError("OpenSSL unavailable or timed out") from exc
    if result.returncode:
        raise ValueError("OpenSSL rejected public material or signature")
    return result.stdout


def verify_policy(raw, signature, root_pubkey, *, board, channel, current=None):
    """Verify exact root-authorized bytes and schema, optionally monotonic to current.

    `root_pubkey` must come from the fixed trusted BOOT/build input, never from
    the policy itself. `current`, when supplied, is previously verified state
    with its exact `raw` bytes and scalar `trust_epoch` (not untrusted JSON).
    Native CMS signer/chain, expiry and package admission belong to task 4.
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


def parse_defaults(raw):
    if not isinstance(raw, bytes) or not 0 < len(raw) <= PAYLOAD or not raw.endswith(b"\n"):
        raise ValueError("expected generated u-boot-initial-env text")
    try:
        lines = raw.decode("ascii").splitlines()
    except UnicodeError as exc:
        raise ValueError("non-ASCII defaults") from exc
    result = {}
    for line in lines:
        if "=" not in line:
            raise ValueError("malformed/blank default")
        name, value = line.split("=", 1)
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) or name in result or name.startswith("epk_") or name.startswith("BOOT_"):
            raise ValueError("duplicate/reserved/invalid default name")
        if any(not 32 <= ord(c) <= 126 for c in value):
            raise ValueError("non-printable default")
        result[name] = value
    return result


def payload(values):
    if len(values) > 256:
        raise ValueError("environment entry count")
    data = b"".join(f"{key}={value}".encode("ascii") + b"\0" for key, value in sorted(values.items())) + b"\0"
    if len(data) > PAYLOAD - HEADROOM:
        raise ValueError("environment exceeds 16 KiB ABI / 2 KiB headroom")
    return data


def record(values, flag):
    data = payload(values)
    data += b"\xff" * (PAYLOAD - len(data))
    return struct.pack("<I", zlib.crc32(data)) + bytes([flag]) + data


def build_seed(defaults, platform, policy_raw, signature, root_pubkey):
    validate_platform(platform)
    policy = verify_policy(policy_raw, signature, root_pubkey,
                           board=platform["board"], channel=platform["channel"])
    values = parse_defaults(defaults)
    values.update({"BOOT_ORDER": "A B", "BOOT_A_LEFT": "3", "BOOT_B_LEFT": "3",
                   "epk_state_schema": "1", "epk_state_generation": "0",
                   "epk_os_floor": str(platform["os_sequence"]),
                   "epk_trust_epoch": str(policy["trust_epoch"]),
                   "epk_trust_policy": base64.b64encode(policy_raw).decode(),
                   "epk_trust_policy_sig": base64.b64encode(signature).decode()})
    if len(values) + 64 > 256:
        raise ValueError("default environment leaves insufficient future entry slots")
    worst = {**values, "epk_state_generation": str(UINT64_MAX), "epk_os_floor": str(UINT64_MAX),
             "epk_trust_epoch": str(UINT64_MAX), "epk_trust_policy": "A" * 8192,
             "epk_trust_policy_sig": "A" * 86 + "=="}
    # Placeholder exists ONLY in the budget fixture, never in seeded records.
    # This aggregate reservation includes name=value NUL overhead for all new
    # boot/runtime/pending/confirmed fields, not just their value characters.
    name = "epk_budget_runtime"
    worst[name] = "x" * (RUNTIME_RESERVE - len(name) - 2)
    used, worst_used = len(payload(values)), len(payload(worst))
    budget = {"schema": 1, "record_bytes": SIZE, "payload_bytes": PAYLOAD,
              "seed_used_bytes": used, "seed_free_bytes": PAYLOAD - used,
              "worst_case_used_bytes": worst_used, "worst_case_free_bytes": PAYLOAD - worst_used,
              "headroom_required_bytes": HEADROOM, "runtime_reserve_bytes": RUNTIME_RESERVE,
              "policy_max_decoded_bytes": POLICY_MAX, "runtime_reserve_entries": 64,
              "defaults_sha256": hashlib.sha256(defaults).hexdigest(),
              "platform_sha256": hashlib.sha256(canonical(platform)).hexdigest(),
              "policy_sha256": hashlib.sha256(policy_raw).hexdigest(),
              "root_spki_sha256": hashlib.sha256(openssl_public_der(root_pubkey)).hexdigest()}
    return record(values, 1), record(values, 2), budget


def openssl_public_der(root_pubkey):
    with tempfile.TemporaryDirectory(prefix="epk-public-") as directory:
        path = Path(directory) / "root.pem"
        path.write_bytes(root_pubkey)
        return openssl("pkey", "-pubin", "-in", path, "-outform", "DER")


def read_bounded(path, maximum):
    with Path(path).open("rb") as stream:
        data = stream.read(maximum + 1)
    if len(data) > maximum:
        raise ValueError("input file size limit")
    return data


def write_artifacts(output, defaults, platform, policy, signature, root):
    """Create a fresh artifact directory; existing paths are never overwritten."""
    a, b, budget = build_seed(defaults, platform, policy, signature, root)
    output = Path(output)
    # Rename a complete sibling only after its files are synced. Refuse even an
    # empty existing directory: this operation is first-flash output, not repair.
    if output.exists() or output.is_symlink():
        raise ValueError("output already exists; refusing replacement")
    temporary = Path(tempfile.mkdtemp(prefix=".epk-seed-", dir=output.parent))
    try:
        artifacts = {"env-a.bin": a, "env-b.bin": b, "budget.json": canonical(budget),
                     "platform.json": canonical(platform), "os-policy.json": policy,
                     "os-policy.sig": signature, "os-index-root.pem": root}
        for name, data in artifacts.items():
            with (temporary / name).open("xb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
        sync_dir(temporary)
        # Atomic publication without replacing even a concurrently created
        # empty directory. This host tool deliberately requires Linux.
        libc = ctypes.CDLL(None, use_errno=True)
        rename = libc.renameat2
        rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        rename.restype = ctypes.c_int
        if rename(-100, os.fsencode(temporary), -100, os.fsencode(output), 1):
            error = ctypes.get_errno()
            raise OSError(error, os.strerror(error))
        sync_dir(output.parent)
    finally:
        shutil.rmtree(temporary, ignore_errors=True)
    return budget


def sync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ["defaults", "platform", "policy", "signature", "root-public-key", "output"]:
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    try:
        budget = write_artifacts(args.output, read_bounded(args.defaults, PAYLOAD),
                                 strict_json(read_bounded(args.platform, 4096), 4096),
                                 read_bounded(args.policy, POLICY_MAX), read_bounded(args.signature, 64),
                                 read_bounded(args.root_public_key, 256))
    except (ValueError, OSError) as exc:
        parser.exit(1, f"bootstrap refused: {exc}\n")
    print(json.dumps(budget, sort_keys=True))


if __name__ == "__main__":
    main()
