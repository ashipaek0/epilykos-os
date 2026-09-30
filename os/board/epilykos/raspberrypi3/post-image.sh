#!/bin/sh
# Buildroot post-image hook (BR2_ROOTFS_POST_IMAGE_SCRIPT) — $BINARIES_DIR
# already has rootfs.ext4 (labeled RootFS-A, per the defconfig), the
# kernel, the dtb, u-boot.bin, boot.scr and the rpi-firmware blobs.
# genimage.cfg assembles all of it into one sdcard.img; the one thing
# genimage can't do itself is relabel a second rootfs copy for slot B,
# so that happens here first.
#
# STATUS: runs successfully as part of a real CI build (os-build.yml),
# producing a partition-correct sdcard.img. Not proof the image boots —
# that's C-BOOT-000, and needs real Pi 3B hardware.
set -e

BOARD_DIR="$(dirname "$0")"

cp "${BINARIES_DIR}/rootfs.ext4" "${BINARIES_DIR}/rootfs-b.ext4"
e2label "${BINARIES_DIR}/rootfs-b.ext4" RootFS-B

GENIMAGE_CFG="${BOARD_DIR}/genimage.cfg"
GENIMAGE_TMP="$(mktemp -d)"

genimage \
	--rootpath "${TARGET_DIR}" \
	--tmppath "${GENIMAGE_TMP}" \
	--inputpath "${BINARIES_DIR}" \
	--outputpath "${BINARIES_DIR}" \
	--config "${GENIMAGE_CFG}"

rm -rf "${GENIMAGE_TMP}"

exit 0
