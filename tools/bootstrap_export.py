#!/usr/bin/env python3
"""Authenticated protocol-1 migration snapshot and baseline-adoption plan.

The CLI obtains live source state over pinned-host SSH, under the existing
application maintenance lock. The receipt is audit evidence, NOT an offline
floor-enrollment credential. No import or card-writing command is provided.
Actual spare-card adoption and protocol-2 source export remain gated on the
immutable slot metadata/runtime transaction integration.
"""
import argparse
import base64
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import select
import shlex
import stat
import subprocess
import sys
import tempfile
import time

MAXIMUM = 65536
ACK_TIMEOUT = 120


def integer(value):
    if type(value) is not int or not 1 <= value <= (1 << 64) - 1:
        raise ValueError("invalid positive sequence")
    return value


def decimal(raw):
    value = raw.decode("ascii").removesuffix("\n")
    if not re.fullmatch("[1-9][0-9]{0,19}", value):
        raise ValueError("missing/malformed sequence floor")
    return integer(int(value))


def env_values(raw):
    values = {}
    for line in raw.decode("ascii").splitlines():
        if not line or line.startswith("#"):
            continue
        if "=" not in line or any(not 32 <= ord(c) <= 126 for c in line):
            raise ValueError("malformed image/state metadata")
        key, value = line.split("=", 1)
        if not re.fullmatch("[A-Z_]+", key) or key in values:
            raise ValueError("duplicate/invalid metadata key")
        values[key] = value
    return values


def identity(value):
    if type(value) is not dict or value.get("channel") not in ("dev", "stable"):
        raise ValueError("app identity channel")
    integer(value.get("sequence"))
    for field, pattern in [("digest", "sha256:[0-9a-f]{64}"), ("image_id", "[0-9a-f]{64}")]:
        if not isinstance(value.get(field), str) or not re.fullmatch(pattern, value[field]):
            raise ValueError("app identity " + field)
    release = value.get("release")
    if not isinstance(release, str) or not re.fullmatch(r"[A-Za-z0-9.+_-]{1,64}", release):
        raise ValueError("app release identity")
    return {key: value[key] for key in ("channel", "sequence", "digest", "image_id", "release")}


def parse_baseline(raw):
    values = env_values(raw)
    ref = values.get("EPILYKOS_IMAGE_REF", "")
    if not re.fullmatch(r"docker.io/[a-z0-9][a-z0-9._/-]{0,127}@sha256:[0-9a-f]{64}", ref):
        raise ValueError("immutable baseline must use a pinned Docker image")
    return identity({"channel": values.get("EPILYKOS_CHANNEL"),
                     "sequence": decimal(values.get("EPILYKOS_SEQUENCE", "").encode()),
                     "release": values.get("EPILYKOS_RELEASE"),
                     "digest": ref.rsplit("@", 1)[1], "image_id": values.get("EPILYKOS_IMAGE_ID")})


def read_state(path, maximum=MAXIMUM):
    """Refuse links/FIFOs and missing required data; never return defaults."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError("state must be a single regular file")
            with os.fdopen(fd, "rb", closefd=False) as stream:
                raw = stream.read(maximum + 1)
            if len(raw) > maximum:
                raise ValueError("source state size limit")
            return raw
        finally:
            os.close(fd)
    except OSError as exc:
        raise ValueError("source state missing/unsafe: " + str(path)) from exc


def exists(path):
    # Broken symlinks count as evidence of state, never as pristine absence.
    return path.exists() or path.is_symlink()


def json_state(raw):
    def pairs(entries):
        result = {}
        for key, value in entries:
            if key in result:
                raise ValueError("duplicate source JSON key")
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=pairs)


@contextmanager
def source_lease(root=Path("/")):
    path = Path(root) / "data/epilykos/app-data/.maintenance.lock"
    try:
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o660)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError("unsafe maintenance lock")
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if os.geteuid() == 0:
                os.fchown(fd, 0, 1000)
            os.fchmod(fd, 0o660)
            yield
        finally:
            os.close(fd)
    except OSError as exc:
        raise ValueError("source maintenance busy/unavailable") from exc


def collect_source(root=Path("/"), *, allow_legacy=False):
    """Caller holds source_lease throughout collection and receipt publication."""
    root = Path(root)
    image = root / "usr/lib/epilykos/image.env"
    raw = read_state(image, 8192)
    values, base = env_values(raw), parse_baseline(raw)
    # Supporting protocol2 here before its fixed metadata/trust reader exists
    # would silently downgrade lost state to a legacy enrollment. Refuse it.
    if not allow_legacy or exists(root / "usr/lib/epilykos/platform.json") or not re.fullmatch(
            r"v?0\.[0-6](?:[.+-][A-Za-z0-9.+_-]+)?", values.get("EPILYKOS_OS_VERSION", "")):
        raise ValueError("only explicitly authorized pre-0.7 protocol-1 sources are supported")
    updater = root / "data/epilykos/updater"
    if exists(updater) and not stat.S_ISDIR(updater.lstat().st_mode):
        raise ValueError("unsafe updater directory")
    if exists(updater / "job.json"):
        raise ValueError("application update in progress/unreconciled")
    current, floor_path, active_path = updater / "current.json", updater / "floor", updater / "active.env"
    app, floor = base, base["sequence"]
    if not exists(current):
        if any(exists(updater / name) for name in ("floor", "active.env", "previous.json")):
            raise ValueError("missing promoted identity with surviving updater state")
    else:
        promoted = identity(json_state(read_state(current)))
        stored = decimal(read_state(floor_path, 32))
        if promoted["channel"] != base["channel"] or stored != promoted["sequence"]:
            raise ValueError("promoted app floor/channel conflict")
        if exists(active_path):
            active = env_values(read_state(active_path, 8192))
            if (active.get("EPILYKOS_ACTIVE_IMAGE_ID") != promoted["image_id"] or
                    decimal(active.get("EPILYKOS_ACTIVE_SEQUENCE", "").encode()) != promoted["sequence"] or
                    active.get("EPILYKOS_ACTIVE_RELEASE") != promoted["release"]):
                raise ValueError("active app differs from promoted identity")
        elif promoted["sequence"] >= base["sequence"]:
            raise ValueError("missing promoted active identity")
        if promoted["sequence"] >= base["sequence"]:
            app = promoted
        floor = max(base["sequence"], stored)
    return {"schema": 1, "boot_protocol": 1, "channel": base["channel"], "app": app,
            "app_floor": floor, "os_floor": None, "trust_epoch": None,
            "source_os_version": values["EPILYKOS_OS_VERSION"]}


def adoption_plan(source, target_baseline):
    """Pure admission calculation, not floor authority or an installed receipt."""
    if (type(source) is not dict or source.get("schema") != 1 or source.get("boot_protocol") != 1 or
            source.get("os_floor") is not None or source.get("trust_epoch") is not None):
        raise ValueError("not a supported authenticated source state")
    app, target = identity(source.get("app")), identity(target_baseline)
    floor = integer(source.get("app_floor"))
    if source.get("channel") != app["channel"] or floor != app["sequence"]:
        raise ValueError("invalid source floor/identity")
    if target["channel"] != app["channel"] or target["sequence"] < floor:
        raise ValueError("new same-channel baseline must meet the existing app floor")
    if target["sequence"] == app["sequence"] and target != app:
        raise ValueError("same app sequence has conflicting identity")
    return {"schema": 1, "installed": False, "channel": app["channel"], "app_floor": floor,
            "source_app": app, "target_app": target,
            "status": "prepared-only; requires explicit spare-card adoption integration"}


def serve(nonce, allow_legacy, *, root=Path("/")):
    if not re.fullmatch("[0-9a-f]{64}", nonce):
        raise ValueError("invalid session nonce")
    with source_lease(root):
        source = collect_source(root, allow_legacy=allow_legacy)
        print(json.dumps({"nonce": nonce, "source": source}, sort_keys=True, separators=(",", ":")), flush=True)
        # Hold exclusion while the client validates and fsyncs its audit receipt.
        if bounded_line(sys.stdin, maximum=65, timeout=ACK_TIMEOUT) != nonce.encode():
            raise ValueError("export session ended before receipt acknowledgement")


def bounded_line(stream, *, maximum, timeout):
    end, data = time.monotonic() + timeout, bytearray()
    while b"\n" not in data:
        remaining = end - time.monotonic()
        if remaining <= 0 or not select.select([stream], [], [], remaining)[0]:
            raise ValueError("source export timed out")
        chunk = os.read(stream.fileno(), min(4096, maximum + 1 - len(data)))
        if not chunk:
            raise ValueError("source export connection closed")
        data.extend(chunk)
        if len(data) > maximum:
            raise ValueError("source export size limit")
    line, remainder = data.split(b"\n", 1)
    if remainder:
        raise ValueError("unexpected source export output")
    return bytes(line)


def receive_line(process, maximum=MAXIMUM):
    return json_state(bounded_line(process.stdout, maximum=maximum, timeout=30))


def export(config, output, *, allow_legacy, target_baseline=None):
    from hardware.os_slot_acceptance import ssh_arguments, write_new
    nonce = secrets.token_hex(32)
    helper = base64.b64encode(Path(__file__).read_bytes()).decode()
    # Execute in one namespace so function globals remain available to serve().
    code = ("import base64;n=dict(__name__='_epk_export');exec(compile(base64.b64decode(" + repr(helper) +
            "),'<epilykos-source-export>','exec'),n);n['serve'](" + repr(nonce) + "," + repr(allow_legacy) + ")")
    command = "sudo -n python3 -u -c " + shlex.quote(code)
    import resource
    def limit_errors():
        resource.setrlimit(resource.RLIMIT_FSIZE, (MAXIMUM, MAXIMUM))
    with tempfile.TemporaryFile() as errors:
        process = subprocess.Popen([*ssh_arguments(config), command], stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=errors, preexec_fn=limit_errors)
        try:
            response = receive_line(process)
            if response.get("nonce") != nonce:
                raise ValueError("source session nonce mismatch")
            source = response["source"]
            receipt = {"schema": 1, "authority": "audit-only; not an enrollment credential", "source": source,
                       "ssh_host": config["host"], "ssh_user": config["user"], "nonce": nonce,
                       "known_hosts_sha256": hashlib.sha256(Path(config["known_hosts_file"]).expanduser().read_bytes()).hexdigest()}
            if target_baseline is not None:
                receipt["adoption_plan"] = adoption_plan(source, target_baseline)
            write_new(output, receipt)
            process.stdin.write((nonce + "\n").encode())
            process.stdin.flush()
            process.stdin.close()
            if process.wait(timeout=15):
                raise ValueError("source did not acknowledge export completion; receipt is audit-only")
        finally:
            if process.poll() is None:
                process.kill()
            process.wait()
            process.stdout.close()
            if not process.stdin.closed:
                process.stdin.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True)
    parser.add_argument("--user", default="epilykos")
    parser.add_argument("--port", type=int, default=22)
    parser.add_argument("--identity-file", required=True)
    parser.add_argument("--known-hosts-file", required=True)
    parser.add_argument("--allow-legacy-protocol-1", action="store_true", required=True)
    parser.add_argument("--target-image-env", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        export(vars(args), args.output, allow_legacy=args.allow_legacy_protocol_1,
               target_baseline=parse_baseline(read_state(args.target_image_env, 8192)) if args.target_image_env else None)
    except (ValueError, OSError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        parser.exit(1, f"bootstrap export refused: {exc}\n")
    print("Source audit receipt saved. No card state was adopted or changed.")


if __name__ == "__main__":
    main()
