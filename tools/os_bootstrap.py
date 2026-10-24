#!/usr/bin/env python3
"""Host-only first-flash seed builder. Never opens a card or repairs live state.

Only public material is accepted. Policy signature verification uses OpenSSL's
Ed25519 implementation; native consumers separately enforce record durability.
Policy verification is shared with the box (epilykos_os_trust).
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
import sys
import tempfile
import zlib

sys.dont_write_bytecode = True
# One trust implementation for the build and the box (OS update design §6).
sys.path.insert(0, str(Path(__file__).resolve().parents[1] /
                       "os/board/epilykos/raspberrypi3/rootfs-overlay/usr/lib/epilykos/python"))
from epilykos_os_trust import (ED25519_SPKI, POLICY_MAX, UINT64_MAX, b64, canonical,  # noqa: E402,F401
                               integer, openssl, strict_json, text, validate_platform, verify_policy)

SIZE = 16384
PAYLOAD = SIZE - 5
HEADROOM = 2048
RUNTIME_RESERVE = 4096


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
