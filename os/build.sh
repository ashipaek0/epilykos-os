#!/bin/sh
# Fetch a pinned Buildroot release and build EpilykosOS against this
# directory as its BR2_EXTERNAL tree.
#
# This is a convenience wrapper, not a hidden build system of its own —
# everything it does is two plain Buildroot commands; read them below
# before running this on a machine you care about. It downloads several
# GB (Buildroot, the toolchain, kernel sources, package tarballs) and a
# from-scratch build commonly takes over an hour even on capable
# hardware. ../.github/workflows/os-build.yml runs this same script in
# CI, with the download and ccache directories cached across runs so
# only the first build after a config change pays the full cost.
#
# Env overrides (CI sets these to cacheable paths; a local run can leave
# them at their defaults):
#   BUILD_DIR      where the Buildroot release is unpacked (default: os/build)
#   BR2_DL_DIR     Buildroot's own package-source download cache
#   MAKE_JOBS      parallel jobs (default: nproc)
#   EPILYKOS_CHANNEL  application manifest to preload, dev or stable
#                  (default: dev; read by the board's post-build.sh)
set -e

BOARD="${1:-raspberrypi3}"
BUILDROOT_VERSION="2026.05.3"
OS_DIR="$(cd "$(dirname "$0")" && pwd)"
BUILD_DIR="${BUILD_DIR:-${OS_DIR}/build}"
BR_DIR="${BUILD_DIR}/buildroot-${BUILDROOT_VERSION}"
MAKE_JOBS="${MAKE_JOBS:-$(nproc 2>/dev/null || echo 2)}"

mkdir -p "${BUILD_DIR}"
export BR2_DL_DIR="${BR2_DL_DIR:-${BUILD_DIR}/dl}"
mkdir -p "${BR2_DL_DIR}"

if [ ! -d "${BR_DIR}" ]; then
	echo "Fetching Buildroot ${BUILDROOT_VERSION}..."
	curl -fsSL "https://buildroot.org/downloads/buildroot-${BUILDROOT_VERSION}.tar.gz" \
		| tar -xz -C "${BUILD_DIR}"
fi

cd "${BR_DIR}"
make BR2_EXTERNAL="${OS_DIR}" "epilykos_${BOARD}_defconfig"
# EPILYKOS_BOOT_PROTOCOL=2 builds a protocol-2 card (OS updates; development):
# the board's protocol2.config on top of its defconfig.
case "${EPILYKOS_BOOT_PROTOCOL:-1}" in
	1) ;;
	2)
		cat "${OS_DIR}/board/epilykos/${BOARD}/protocol2.config" >> .config
		make BR2_EXTERNAL="${OS_DIR}" olddefconfig
		grep -qx 'BR2_EPILYKOS_BOOT_PROTOCOL_2=y' .config || { echo "protocol 2 not enabled" >&2; exit 1; }
		;;
	*) echo "EPILYKOS_BOOT_PROTOCOL must be 1 or 2" >&2; exit 1 ;;
esac
make BR2_EXTERNAL="${OS_DIR}" -j"${MAKE_JOBS}"

echo
echo "Image (if the build succeeded): ${BR_DIR}/output/images/sdcard.img"
