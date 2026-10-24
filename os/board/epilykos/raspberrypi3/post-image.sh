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

GENIMAGE_CFG="${BOARD_DIR}/genimage.cfg"
PROTOCOL2=false
grep -qx 'BR2_EPILYKOS_BOOT_PROTOCOL_2=y' "${BR2_CONFIG}" && PROTOCOL2=true

# -- Protocol 2: seeded environment and pinned trust root --
# Both U-Boot environment copies ship valid: this loader's generated
# defaults, the A/B counters, the OS floor (this image's OS sequence) and
# the trust policy signed by the offline OS index root, whose public key is
# pinned on BOOT (tools/os_bootstrap.py verifies the policy against it).
# EPILYKOS_TRUST_DIR holds os-policy.json, os-policy.sig and
# os-index-root.pem (the maintainer's, or tools/os_test_trust.py's for a
# test card); without them the build stops rather than ship blank state.
if ${PROTOCOL2}; then
	REPO_DIR="$(cd "${BOARD_DIR}/../../../.." && pwd)"
	PLATFORM_JSON="${TARGET_DIR}/usr/lib/epilykos/platform.json"
	CHANNEL=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["channel"])' "${PLATFORM_JSON}")
	TRUST_DIR="${EPILYKOS_TRUST_DIR:-${REPO_DIR}/keys/os-release/${CHANNEL}}"
	for f in os-policy.json os-policy.sig os-index-root.pem; do
		test -f "${TRUST_DIR}/${f}" || { echo "post-image: protocol 2 needs ${TRUST_DIR}/${f} (EPILYKOS_TRUST_DIR)" >&2; exit 1; }
	done
	SEED="${BINARIES_DIR}/protocol2-seed"
	rm -rf "${SEED}"
	python3 "${REPO_DIR}/tools/os_bootstrap.py" \
		--defaults "${BINARIES_DIR}/u-boot-initial-env" \
		--platform "${PLATFORM_JSON}" \
		--policy "${TRUST_DIR}/os-policy.json" \
		--signature "${TRUST_DIR}/os-policy.sig" \
		--root-public-key "${TRUST_DIR}/os-index-root.pem" \
		--output "${SEED}"
	cp "${SEED}/env-a.bin" "${SEED}/env-b.bin" "${SEED}/os-index-root.pem" "${BINARIES_DIR}/"
	GENIMAGE_CFG="${BOARD_DIR}/genimage-protocol2.cfg"
fi

GENIMAGE_TMP="$(mktemp -d)"
trap 'rm -rf "${GENIMAGE_TMP}"' EXIT

genimage \
	--rootpath "${TARGET_DIR}" \
	--tmppath "${GENIMAGE_TMP}" \
	--inputpath "${BINARIES_DIR}" \
	--outputpath "${BINARIES_DIR}" \
	--config "${GENIMAGE_CFG}"

# The card as flashed: layout, seeded environment, boot script, trust root
# on BOOT and both slots' Linux (tools/check_card_image.py).
if ${PROTOCOL2}; then
	python3 "${REPO_DIR}/tools/check_card_image.py" \
		--image "${BINARIES_DIR}/sdcard.img" \
		--rootfs-a "${BINARIES_DIR}/rootfs.ext4" --rootfs-b "${BINARIES_DIR}/rootfs-b.ext4" \
		--seed "${SEED}" --boot-script "${BOARD_DIR}/boot-slot.cmd"
fi
