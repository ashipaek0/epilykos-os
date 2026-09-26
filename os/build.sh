#!/bin/sh
# Fetch a pinned Buildroot release and build EpilykosOS against this
# directory as its BR2_EXTERNAL tree.
#
# This is a convenience wrapper, not a hidden build system of its own —
# everything it does is two plain Buildroot commands; read them below
# before running this on a machine you care about. It downloads several
# GB (Buildroot, the toolchain, kernel sources, package tarballs) and a
# from-scratch build commonly takes over an hour even on capable
# hardware; that is why this doesn't run in hosted CI (see
# ../.github/workflows/os-build-check.yml, which only lints).
set -e

BOARD="${1:-raspberrypi3}"
BUILDROOT_VERSION="2024.02.10"
OS_DIR="$(cd "$(dirname "$0")" && pwd)"
BUILD_DIR="${OS_DIR}/build"
BR_DIR="${BUILD_DIR}/buildroot-${BUILDROOT_VERSION}"

mkdir -p "${BUILD_DIR}"

if [ ! -d "${BR_DIR}" ]; then
	echo "Fetching Buildroot ${BUILDROOT_VERSION}..."
	curl -fsSL "https://buildroot.org/downloads/buildroot-${BUILDROOT_VERSION}.tar.gz" \
		| tar -xz -C "${BUILD_DIR}"
fi

cd "${BR_DIR}"
make BR2_EXTERNAL="${OS_DIR}" "epilykos_${BOARD}_defconfig"
make BR2_EXTERNAL="${OS_DIR}"

echo
echo "Image (if the build succeeded): ${BR_DIR}/output/images/sdcard.img"
