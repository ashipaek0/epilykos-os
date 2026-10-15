#!/bin/sh
# Buildroot post-image hook (BR2_ROOTFS_POST_IMAGE_SCRIPT). $BINARIES_DIR
# already has rootfs.ext4 (labelled ROOT-A by the defconfig), Image, the
# DTB, u-boot.bin, boot.scr and the rpi-firmware blobs; genimage.cfg
# assembles them into sdcard.img. The one thing genimage can't do is
# relabel a second rootfs copy for slot B, so that happens here first.
# (Slots are addressed by PARTUUID, not label; the label is for humans.)
set -e

BOARD_DIR="$(dirname "$0")"

# The Wi-Fi template users copy to wifi.txt on the BOOT partition.
cp "${BOARD_DIR}/wifi-example.txt" "${BINARIES_DIR}/wifi-example.txt"

cp "${BINARIES_DIR}/rootfs.ext4" "${BINARIES_DIR}/rootfs-b.ext4"
e2label "${BINARIES_DIR}/rootfs-b.ext4" ROOT-B

GENIMAGE_TMP="$(mktemp -d)"
trap 'rm -rf "${GENIMAGE_TMP}"' EXIT

genimage \
	--rootpath "${TARGET_DIR}" \
	--tmppath "${GENIMAGE_TMP}" \
	--inputpath "${BINARIES_DIR}" \
	--outputpath "${BINARIES_DIR}" \
	--config "${BOARD_DIR}/genimage.cfg"
