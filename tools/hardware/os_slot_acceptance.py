#!/usr/bin/env python3
"""Collect manual Pi3 slot-acceptance evidence over pinned-key SSH.

Read-only: no flash, installs, reboots, corruption or power switching. The
operator prepares each test using its reviewed procedure and cuts power by
hand. Observations remain unreviewed; this tool cannot grant release approval.
"""
import argparse
import base64
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import stat
import subprocess
import sys

# Design §12 (OS updates from Settings), one checkpoint per hardware case.
# Each is observed by hand before and after; none is ever marked passed here.
CHECKPOINTS = {
    # Slots, boot health and the loader
    "boot-a", "boot-b", "healthy-reboots", "bad-dtb", "bad-kernel", "bad-app", "hung-kernel",
    "supervisor-hang", "data-mount-failure", "both-slots-exhausted",
    # The online update journey
    "good-update", "download-power-cut", "install-power-cut", "ready-power-cut", "restart-power-cut",
    "trying-power-cut", "confirm-power-cut", "trial-failure-rollback", "worker-killed-rauc-alive",
    "pending-backup-at-restart", "discard",
    # Trust
    "refused-packages", "revocation-after-fallback", "policy-copy-loss", "interrupted-policy-update",
    # App and data
    "app-survives-update", "factory-reset", "factory-reset-trying", "db-fallback-compat",
    # Measurements (R18)
    "measurements", "soak-72h"}
PAIRED = CHECKPOINTS - {"boot-a", "boot-b", "measurements"}
LIMIT = 128 * 1024

# Fixed commands only; device configuration cannot supply shell commands.
COLLECTOR = r'''
import json, os, pathlib, subprocess

def read(path, size=8192):
    with open(path, "rb") as stream:
        raw = stream.read(size + 1)
    if len(raw) > size:
        raise ValueError("device evidence size limit")
    return raw.decode("utf-8").strip("\x00\n ")

snapshot = {"schema": 1,
    "model": read("/proc/device-tree/model"),
    "card_cid": read("/sys/block/mmcblk0/device/cid"),
    "boot_id": read("/proc/sys/kernel/random/boot_id"),
    "cmdline": read("/proc/cmdline"), "kernel": os.uname().release,
    "platform": json.loads(read("/usr/lib/epilykos/platform.json", 4096)), "commands": {}}
for name, argv in [
    ("root_mount", ["findmnt", "--json", "--output", "SOURCE,TARGET,FSTYPE,OPTIONS", "--mountpoint", "/"]),
    ("data_mount", ["findmnt", "--json", "--output", "SOURCE,TARGET,FSTYPE,OPTIONS", "--mountpoint", "/data"]),
    ("rauc", ["rauc", "status"]),
    ("env", ["fw_printenv", "BOOT_ORDER", "BOOT_A_LEFT", "BOOT_B_LEFT", "epk_state_schema",
             "epk_state_generation", "epk_os_floor", "epk_trust_epoch", "epk_os_pending", "epk_os_target",
             "epk_os_from", "epk_os_target_sequence", "epk_os_phase", "epk_os_trial_started"]),
    ("os_update", ["python3", "-c", "import json,sys; sys.path.insert(0,'/usr/lib/epilykos/python'); "
                   "import epilykos_os_update as u; print(json.dumps(u.view(resume=False), sort_keys=True))"]),
    ("os_guard", ["cat", "/data/epilykos/os-updater/public/guard.json"]),
    ("os_health", ["cat", "/run/epilykos/os-health.json"]),
    ("services", ["systemctl", "show", "rauc", "epilykos-os-update", "epilykos-boot-health",
                  "--property=Id,ActiveState,SubState,Result,MemoryPeak,InvocationID"]),
    ("memory", ["free", "-b"]),
    ("data_space", ["df", "-B1", "/data"]),
    ("app", ["systemctl", "show", "epilykos", "--property=ActiveState,SubState,Result"]),
    ("journal", ["journalctl", "--no-pager", "-b", "-n", "120", "-u", "epilykos",
                 "-u", "rauc", "-u", "epilykos-mark-good", "-u", "epilykos-boot-health",
                 "-u", "epilykos-os-update", "-u", "epilykos-hostd"])]:
    try:
        # A regular temporary file bounds memory even if a misbehaving command
        # emits too much. A POSIX file-size limit also bounds disk usage.
        import resource, tempfile
        with tempfile.TemporaryFile() as output:
            def limit_output():
                resource.setrlimit(resource.RLIMIT_FSIZE, (16384, 16384))
            result = subprocess.run(argv, stdout=output, stderr=subprocess.STDOUT,
                                    timeout=15, preexec_fn=limit_output)
            output.seek(0)
            snapshot["commands"][name] = {"returncode": result.returncode,
                "output": output.read(16384).decode("utf-8", errors="replace")}
    except (OSError, subprocess.TimeoutExpired) as exc:
        snapshot["commands"][name] = {"error": str(exc)}
print(json.dumps(snapshot, sort_keys=True, separators=(",", ":")))
'''


def read_json(path):
    with Path(path).open("rb") as stream:
        raw = stream.read(LIMIT + 1)
    if len(raw) > LIMIT:
        raise ValueError("evidence/config size limit")
    return json.loads(raw)


def ssh_arguments(config):
    host, user, port = config.get("host"), config.get("user"), config.get("port")
    if not isinstance(host, str) or not re.fullmatch(r"[A-Za-z0-9:][A-Za-z0-9:._-]{0,252}", host):
        raise ValueError("invalid SSH host")
    if not isinstance(user, str) or not re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", user):
        raise ValueError("invalid SSH user")
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("invalid SSH port")
    paths = {}
    for field in ["identity_file", "known_hosts_file"]:
        try:
            path = Path(config[field]).expanduser().resolve(strict=True)
        except (OSError, TypeError) as exc:
            raise ValueError("SSH identity/known-hosts file missing") from exc
        if not path.is_file():
            raise ValueError("SSH identity/known-hosts file missing")
        paths[field] = path
    return ["ssh", "-F", "/dev/null", "-T", "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes",
            "-o", "StrictHostKeyChecking=yes", "-o", "GlobalKnownHostsFile=/dev/null",
            "-o", "UserKnownHostsFile=" + str(paths["known_hosts_file"]), "-o", "ConnectTimeout=10",
            "-o", "ConnectionAttempts=1", "-o", "ServerAliveInterval=10", "-o", "ServerAliveCountMax=2",
            "-i", str(paths["identity_file"]), "-p", str(port), user + "@" + host]


def collect(config):
    encoded = base64.b64encode(COLLECTOR.encode()).decode()
    command = "sudo -n python3 -c " + shlex.quote("import base64;exec(compile(base64.b64decode(" + repr(encoded) + "),'<epilykos-evidence>','exec'))")
    # Capture both pipes in bounded files, with no stdin/password prompts.
    import resource
    import tempfile
    def limit_output():
        resource.setrlimit(resource.RLIMIT_FSIZE, (LIMIT, LIMIT))
    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
        try:
            result = subprocess.run([*ssh_arguments(config), command], stdin=subprocess.DEVNULL,
                                    stdout=output, stderr=errors, timeout=110, preexec_fn=limit_output)
            output.seek(0)
            errors.seek(0)
            if result.returncode:
                return {"error": "SSH collection failed", "returncode": result.returncode,
                        "detail": errors.read(2048).decode(errors="replace")}
            raw = output.read(LIMIT + 1)
            if len(raw) >= LIMIT:
                raise ValueError("SSH output limit reached")
            return json.loads(raw)
        except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
            return {"error": str(exc)}


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_new(path, value):
    raw = json.dumps(value, sort_keys=True, indent=2, allow_nan=False).encode() + b"\n"
    if len(raw) > LIMIT:
        raise ValueError("evidence size limit")
    with Path(path).open("xb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    sync_directory(Path(path).parent)


def now():
    return datetime.now(timezone.utc).isoformat()


def initialize(directory, config):
    ssh_arguments(config)
    for field, pattern in [("image_sha256", "[0-9a-f]{64}"), ("expected_card_cid", "[0-9a-f]{32}"),
                           ("expected_source", "[0-9a-f]{40}")]:
        if not isinstance(config.get(field), str) or not re.fullmatch(pattern, config[field]):
            raise ValueError("missing/invalid expected identity: " + field)
    with Path(config["image"]).expanduser().open("rb") as image:
        digest = hashlib.file_digest(image, "sha256").hexdigest()
    if digest != config["image_sha256"]:
        raise ValueError("local image SHA256 does not match expected artifact")
    directory = Path(directory)
    if directory.exists() or directory.is_symlink():
        raise ValueError("evidence directory already exists")
    directory.mkdir(mode=0o700)
    (directory / "events").mkdir(mode=0o700)
    write_new(directory / "identity.json", {"schema": 1, "created_at": now(),
              "image_sha256": digest, "hardware_accepted": False, "config": config,
              "checkpoint_status": {name: "pending" for name in sorted(CHECKPOINTS)}})
    sync_directory(directory.parent)


def record(directory, checkpoint, phase, snapshot, note):
    directory = Path(directory)
    if checkpoint not in CHECKPOINTS or phase not in ("before", "after") or not isinstance(note, str) or not note.strip() or len(note) > 4096:
        raise ValueError("explicit checkpoint, phase and operator observation required")
    identity = read_json(directory / "identity.json")
    config = identity["config"]
    if type(snapshot) is not dict:
        raise ValueError("malformed snapshot")
    failed = "error" in snapshot
    if not failed:
        if snapshot.get("card_cid") != config["expected_card_cid"] or not str(snapshot.get("model", "")).startswith("Raspberry Pi 3 Model B Rev "):
            raise ValueError("device/card identity mismatch")
        if not isinstance(snapshot.get("boot_id"), str) or not re.fullmatch(r"[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}", snapshot["boot_id"]):
            raise ValueError("missing/invalid boot identity")
        # A different source after a deliberate rollback is expected and is
        # preserved for review. The initial before snapshot must bind the image.
        if (phase == "before" or checkpoint in {"boot-a", "boot-b"}) and snapshot.get("platform", {}).get("source") != config["expected_source"]:
            raise ValueError("initial running OS source does not match configured image")
    fd = os.open(directory / ".lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode) or os.fstat(fd).st_nlink != 1:
            raise ValueError("invalid evidence lock")
        fcntl.flock(fd, fcntl.LOCK_EX)
        events = sorted((directory / "events").glob("*.json"))
        if checkpoint in PAIRED and phase == "after" and not any(
                (entry := read_json(path))["checkpoint"] == checkpoint and entry["phase"] == "before"
                and entry["assessment"] != "collection-failed" for path in events):
            raise ValueError("record a valid before checkpoint first")
        write_new(directory / "events" / f"{len(events) + 1:06d}.json",
                  {"schema": 1, "observed_at": now(), "checkpoint": checkpoint, "phase": phase,
                   "operator_note": note, "snapshot": snapshot,
                   "assessment": "collection-failed" if failed else "unreviewed"})
    finally:
        os.close(fd)


def report(directory):
    """What has been observed per checkpoint. Never a pass: review decides."""
    directory = Path(directory)
    identity = read_json(directory / "identity.json")
    seen = {name: set() for name in CHECKPOINTS}
    failed = {name: 0 for name in CHECKPOINTS}
    for path in sorted((directory / "events").glob("*.json")):
        event = read_json(path)
        if event.get("assessment") == "collection-failed":
            failed[event["checkpoint"]] += 1
        else:
            seen[event["checkpoint"]].add(event["phase"])
    status = {}
    for name in sorted(CHECKPOINTS):
        needed = {"before", "after"} if name in PAIRED else {"after"}
        status[name] = ("observed-unreviewed" if needed <= seen[name]
                        else "partial" if seen[name] else "collection-failed" if failed[name] else "pending")
    return {"schema": 1, "image_sha256": identity["image_sha256"], "hardware_accepted": False,
            "checkpoints": status,
            "pending": sorted(name for name, value in status.items() if value != "observed-unreviewed")}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    init = sub.add_parser("init")
    init.add_argument("--config", type=Path, required=True)
    init.add_argument("--output", type=Path, required=True)
    observe = sub.add_parser("observe")
    observe.add_argument("--run", type=Path, required=True)
    observe.add_argument("--checkpoint", choices=sorted(CHECKPOINTS), required=True)
    observe.add_argument("--phase", choices=["before", "after"], required=True)
    observe.add_argument("--note", required=True)
    summary = sub.add_parser("report")
    summary.add_argument("--run", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.action == "report":
            print(json.dumps(report(args.run), indent=2, sort_keys=True))
            return
        if args.action == "init":
            initialize(args.output, read_json(args.config))
        else:
            snapshot = collect(read_json(args.run / "identity.json")["config"])
            record(args.run, args.checkpoint, args.phase, snapshot, args.note)
            if "error" in snapshot:
                parser.exit(1, "collection failed; error recorded for review\n")
    except (ValueError, OSError, KeyError, TypeError) as exc:
        parser.exit(1, f"acceptance evidence refused: {exc}\n")
    print("Evidence recorded; hardware acceptance remains pending review.")


if __name__ == "__main__":
    main()
