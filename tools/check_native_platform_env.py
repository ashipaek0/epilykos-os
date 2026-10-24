#!/usr/bin/env python3
"""Build and exercise pinned native env consumers; never accesses a device.

Requires cc/make/patch, bison/flex/m4, an AArch64 cross compiler, Meson/Ninja,
pkg-config and GLib/GIO/OpenSSL development headers. Downloads are hash pinned.
"""
import argparse
import hashlib
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
BOARD = ROOT / "os/board/epilykos/raspberrypi3"
SOURCES = {
    "u-boot-2025.10.tar.bz2": (
        "https://ftp.denx.de/pub/u-boot/u-boot-2025.10.tar.bz2",
        "b4f032848e56cc8f213ad59f9132c084dbbb632bc29176d024e58220e0efdf4a"),
    "u-boot-2026.04.tar.bz2": (
        "https://ftp.denx.de/pub/u-boot/u-boot-2026.04.tar.bz2",
        "ac7c04b8b7004923b00a4e5d6699c5df4d21233bac9fda690d8cfbc209fff2fd"),
    "rauc-1.15.2.tar.xz": (
        "https://github.com/rauc/rauc/releases/download/v1.15.2/rauc-1.15.2.tar.xz",
        "127a24cde208c65b837ae978c695a00730f1094ee8b6c7d48cf58ef846eae340"),
}


def run(args, **kwargs):
    print("+ " + shlex.join(map(str, args)), flush=True)
    return subprocess.run(list(map(str, args)), check=True, **kwargs)


def source(cache, work, name):
    url, expected = SOURCES[name]
    archive = cache / name
    if not archive.exists():
        with tempfile.NamedTemporaryFile(dir=cache, delete=False) as temporary:
            part = Path(temporary.name)
            try:
                with urllib.request.urlopen(url, timeout=60) as response:
                    shutil.copyfileobj(response, temporary)
                temporary.close()
                with part.open("rb") as stream:
                    if hashlib.file_digest(stream, "sha256").hexdigest() != expected:
                        raise ValueError(f"source hash mismatch: {name}")
                part.replace(archive)
            finally:
                part.unlink(missing_ok=True)
    with archive.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != expected:
            raise ValueError(f"cached source hash mismatch: {name}")
    with tarfile.open(archive) as tar:
        tar.extractall(work, filter="data")
    return work / name.removesuffix(".tar.bz2").removesuffix(".tar.xz")


def hook_makefile(work, uboot, fw, enabled):
    """Exercise the actual external.mk hooks and paired make/fragment options."""
    path = work / ("hooks-on.mk" if enabled else "hooks-off.mk")
    path.write_text(
        f"BR2_EXTERNAL_EPILYKOS_PATH := {ROOT}/os\n"
        "UBOOT_VERSION := 2025.10\nUBOOT_TOOLS_VERSION := 2026.04\nINSTALL := install\n"
        f"BR2_EPILYKOS_NATIVE_ENV_GUARD := {'y' if enabled else ''}\n"
        f"UBOOT_KCONFIG_FRAGMENT_FILES := {BOARD}/uboot.fragment\n"
        f"include {ROOT}/os/external.mk\n"
        f"{uboot}/.epk-copy:\n\t$(EPILYKOS_COPY_UBOOT_ENV_GUARD)\n"
        f"{fw}/.epk-copy:\n\t$(EPILYKOS_COPY_FW_ENV_GUARD)\n"
        f"{uboot}/.epk-config-check:\n\t$(EPILYKOS_CHECK_NATIVE_ENV_GUARD)\n"
        ".PHONY: options\noptions:\n"
        "\t@printf '%s\\n' '$(UBOOT_KCONFIG_FRAGMENT_FILES)' '$(UBOOT_TOOLS_MAKE_OPTS)'\n")
    return path


def patch_source(directory, package, version):
    for patch in sorted((BOARD / "patches" / package / version).glob("*.patch")):
        run(["patch", "--batch", "--fuzz=0", "-p1", "-i", patch], cwd=directory)


def fw_build(directory, options):
    for name in ["include/config", "include/generated", "include/asm"]:
        (directory / name).mkdir(parents=True, exist_ok=True)
    (directory / "include/config.h").write_text("#include <linux/kconfig.h>\n")
    (directory / "include/config/auto.conf").touch()
    (directory / "include/generated/autoconf.h").touch()
    (directory / "include/asm/linkage.h").touch()
    run(["make", "-C", directory, "envtools", "no-dot-config-targets=envtools",
         "HOSTCC=cc", "CC=cc", "STRIP=strip", *options])
    return directory / "tools/env/fw_printenv"


def legacy_smoke(binary, work):
    from test_platform_env import OFFSETS, SIZE, record
    card = work / "legacy-card.img"
    with card.open("wb") as stream:
        for offset, flag in zip(OFFSETS, [1, 2]):
            stream.seek(offset)
            stream.write(record({"BOOT_ORDER": "A B", "BOOT_A_LEFT": "3"}, flag))
    config = work / "legacy.config"
    config.write_text("".join(f"{card} {offset:#x} {SIZE:#x}\n" for offset in OFFSETS))
    result = run([binary, "-c", config, "-l", work, "-n", "BOOT_A_LEFT"], capture_output=True, text=True)
    if result.stdout.strip() != "3":
        raise ValueError("guard-off build changed protocol-1 native reads")
    setter_dir = work / "legacy-tools"
    setter_dir.mkdir()
    setter = setter_dir / "fw_setenv"
    setter.symlink_to(binary)
    run([setter, "-c", config, "-l", work, "BOOT_A_LEFT", "2"])
    result = run([binary, "-c", config, "-l", work, "-n", "BOOT_A_LEFT"], capture_output=True, text=True)
    if result.stdout.strip() != "2":
        raise ValueError("guard-off build changed protocol-1 native writes")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-dir", type=Path, default=ROOT / "os/build/native-env")
    parser.add_argument("--cross-compile", default="aarch64-linux-gnu-")
    args = parser.parse_args()
    cache = args.cache_dir.resolve()
    if any(c.isspace() for c in str(ROOT) + str(cache)):
        parser.error("Buildroot/native make paths must not contain whitespace")
    cache.mkdir(parents=True, exist_ok=True)
    for tool in ["cc", "make", "patch", "bison", "flex", "m4", "meson", "ninja",
                 "mksquashfs", "mke2fs", "debugfs",
                 args.cross_compile + "gcc", args.cross_compile + "nm"]:
        if not shutil.which(tool):
            parser.error(f"missing build tool: {tool}")
    with tempfile.TemporaryDirectory(prefix="integration-", dir=cache) as temporary:
        work = Path(temporary)
        uboot = source(cache, work, "u-boot-2025.10.tar.bz2")
        fw = source(cache, work, "u-boot-2026.04.tar.bz2")
        rauc_source = source(cache, work, "rauc-1.15.2.tar.xz")
        patch_source(uboot, "uboot", "2025.10")
        patch_source(fw, "uboot-tools", "2026.04")
        hooks = hook_makefile(work, uboot, fw, True)
        run(["make", "-f", hooks, uboot / ".epk-copy", fw / ".epk-copy"])
        result = run(["make", "--no-print-directory", "-s", "-f", hooks, "options"],
                     capture_output=True, text=True)
        fragments, options = result.stdout.splitlines()
        binary = fw_build(fw, shlex.split(options))
        # Compile the actual patched Pi3 import/save paths with an ARM64 compiler.
        make = ["make", "-C", uboot, "CROSS_COMPILE=" + args.cross_compile]
        run([*make, "rpi_3_defconfig"])
        run([uboot / "scripts/kconfig/merge_config.sh", "-m", uboot / ".config",
             *shlex.split(fragments)], cwd=uboot)
        run([*make, "olddefconfig"])
        run(["make", "-f", hooks, uboot / ".epk-config-check"])
        off = hook_makefile(work, uboot, fw, False)
        mismatch = subprocess.run(["make", "-f", str(off), str(uboot / ".epk-config-check")],
                                  capture_output=True, text=True)
        if mismatch.returncode == 0:
            raise ValueError("Buildroot accepted guarded loader with unguarded fw-tools")
        run([*make, "-j2", "KCFLAGS=-fstack-usage", "env/common.o", "env/mmc.o", "env/epk_env.o", "env/epk_halt.o"])
        for obj, symbol in [("common", "epk_env_select"), ("mmc", "epk_env_commit_snapshot")]:
            symbols = run([args.cross_compile + "nm", "-u", uboot / f"env/{obj}.o"],
                          capture_output=True, text=True).stdout
            if symbol not in symbols.split():
                raise ValueError(f"guard missing from actual ARM64 {obj}.o")
        for report in sorted((uboot / "env").glob("*.su")):
            print(report.name + "\n" + report.read_text(), flush=True)
        run([*make, "u-boot-initial-env"])
        run([sys.executable, ROOT / "tools/test_bootstrap_adoption.py",
             "--defaults", uboot / "u-boot-initial-env"])
        # Minimal real RAUC CLI build: bootchooser code and bundle creation
        # (OS packages, tools/test_os_bundle.py), no daemon/network.
        env = {**os.environ, "GIT_CEILING_DIRECTORIES": str(work)}
        rauc_build = work / "rauc-build"
        run(["meson", "setup", rauc_build, rauc_source, "-Dservice=false",
             "-Dnetwork=false", "-Dstreaming=false", "-Djson=disabled", "-Dgpt=disabled",
             "-Dtests=false", "-Dpkcs11_engine=false"], env=env)
        run(["meson", "compile", "-C", rauc_build], env=env)
        run([rauc_build / "rauc", "--version"])
        faults = work / "native-env-faults.so"
        run(["cc", "-shared", "-fPIC", "-Wall", "-Wextra", "-Werror",
             ROOT / "tools/native_env_faults.c", "-ldl", "-o", faults])
        run([sys.executable, ROOT / "tools/test_native_platform_env.py", "--fw-tools", binary,
             "--fault-library", faults, "--rauc", rauc_build / "rauc"])
        run([sys.executable, ROOT / "tools/test_os_bundle.py", "--rauc", rauc_build / "rauc"])
        # The same patched sources must still support existing unseeded cards
        # when the default development switch is off.
        legacy = fw_build(fw, ["EPILYKOS_ENV_GUARD="])
        legacy_smoke(legacy, work)
        run([uboot / "scripts/config", "--file", uboot / ".config", "--disable", "EPILYKOS_ENV_GUARD"])
        run([*make, "olddefconfig"])
        run(["make", "-f", off, uboot / ".epk-config-check"])
        run([*make, "-j2", "env/common.o", "env/mmc.o"])
    print("PASS pinned native fw-tools/RAUC, OS packages and ARM64 loader compilation (guard on/off)")


if __name__ == "__main__":
    main()
