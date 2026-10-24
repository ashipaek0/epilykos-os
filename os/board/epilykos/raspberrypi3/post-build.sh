#!/bin/sh
# Buildroot post-build hook (BR2_ROOTFS_POST_BUILD_SCRIPT): runs against
# the staged target rootfs ($TARGET_DIR) before it is packed into an image.
#
# 1. fstab for BOOT and DATA.
# 2. The Epilykos application image, preloaded (C-RUNTIME-001,
#    C-DATA-002): the digest pinned by manifests/<channel>.yaml is copied
#    for linux/arm64 into an OCI archive in the rootfs, and the IDs the
#    service needs are written to /usr/lib/epilykos/image.env. First boot
#    loads the archive into Podman's storage on DATA with no network.
#
# EPILYKOS_CHANNEL selects the manifest: "stable" (manifests/stable.yaml,
# what main builds ship) or "dev" (the default). Needs skopeo on the
# build host.
set -e

BOARD_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "${BOARD_DIR}/../../../.." && pwd)"
CHANNEL="${EPILYKOS_CHANNEL:-dev}"
MANIFEST="${REPO_DIR}/manifests/${CHANNEL}.yaml"

# The root filesystem stays read-only (C-BOOT-001) and is checked at
# boot. DATA is the only persistent read-write filesystem every slot
# mounts; it is checked and repaired at boot (fsck.repair=yes, boot.cmd).
# Both are addressed by PARTUUID (fixed disk signature, genimage.cfg).
# BOOT is mounted read-only and only for inspection, so it must not be
# able to fail the boot. The bind mounts put the state that must survive
# a reboot on DATA; epilykos-data-layout creates their sources first.
mkdir -p "${TARGET_DIR}/data" "${TARGET_DIR}/boot"
DATA_BIND="none    bind,x-systemd.requires=epilykos-data-layout.service,x-systemd.after=epilykos-data-layout.service"
cat > "${TARGET_DIR}/etc/fstab" <<-FSTAB
	# <file system>              <mount point>  <type>  <options>                <dump> <pass>
	/dev/root                    /              auto    ro                       0      1
	PARTUUID=45504c4b-04         /data          ext4    defaults,noatime         0      2
	PARTUUID=45504c4b-01         /boot          vfat    ro,nofail                0      0
	/data/journal                /var/log/journal           ${DATA_BIND} 0 0
	/data/bluetooth              /var/lib/bluetooth         ${DATA_BIND} 0 0
	/data/timesync               /var/lib/systemd/timesync  ${DATA_BIND} 0 0
FSTAB

# sudo reads the epilykos rule from /etc/sudoers.d (C-ACCESS-001); the
# drop-in must not be group- or world-writable.
grep -q '^@includedir /etc/sudoers.d' "${TARGET_DIR}/etc/sudoers" ||
	echo '@includedir /etc/sudoers.d' >> "${TARGET_DIR}/etc/sudoers"
chmod 0440 "${TARGET_DIR}/etc/sudoers.d/epilykos"

# Keep this owner-writable for Buildroot's unprivileged staging cleanup,
# including rebuilds of a target previously set to 0555. The BlueZ drop-in
# requests the same mode, so systemd need not chmod our read-only root.
# Runtime write protection comes from the read-only root mount.
[ ! -d "${TARGET_DIR}/etc/bluetooth" ] || chmod 0755 "${TARGET_DIR}/etc/bluetooth"

# The clock never starts before this build: systemd moves it forward to
# this file's time at boot. Without it a fresh card started at systemd's
# own release date (months back) until network time arrived; after that,
# the time saved on DATA (/data/timesync) is later and wins.
touch "${TARGET_DIR}/usr/lib/clock-epoch"

# -- Preloaded application image --
test -f "${MANIFEST}" || { echo "post-build: no manifest ${MANIFEST}" >&2; exit 1; }

# The manifest is generated (tools/sync_app_release.py) with a fixed
# layout: images.epilykos.{digest,source}. Read just those two keys
# rather than depend on a YAML library on the build host.
manifest_value() {
	awk -v want="$1" '
		/^images:/ { in_images = 1; next }
		in_images && /^[^ ]/ { in_images = 0 }
		in_images && /^  [a-z]/ { in_app = ($1 == "epilykos:") ; next }
		in_images && in_app && $1 == want ":" { print $2; exit }
	' "${MANIFEST}"
}
RELEASE=$(awk '$1 == "release:" { print $2; exit }' "${MANIFEST}")
SEQUENCE=$(awk '$1 == "sequence:" { print $2; exit }' "${MANIFEST}")
case "${SEQUENCE}" in
	''|*[!0-9]*) echo "post-build: no sequence in ${MANIFEST}" >&2; exit 1 ;;
esac
DIGEST=$(manifest_value digest)
SOURCE=$(manifest_value source)
case "${DIGEST}" in
	sha256:[0-9a-f]*) ;;
	*) echo "post-build: no epilykos digest in ${MANIFEST}" >&2; exit 1 ;;
esac
test -n "${SOURCE}" || { echo "post-build: no epilykos source in ${MANIFEST}" >&2; exit 1; }

command -v skopeo >/dev/null || {
	echo "post-build: skopeo is required to preload the Epilykos image" >&2
	exit 1
}

APP_DIR="${TARGET_DIR}/usr/lib/epilykos"
ARCHIVE="${APP_DIR}/epilykos.oci.tar"
REF="docker.io/${SOURCE}@${DIGEST}"
mkdir -p "${APP_DIR}"
rm -f "${ARCHIVE}"

echo "post-build: preloading ${REF} (linux/arm64, ${CHANNEL} ${RELEASE})"
# A pinned digest names a multi-arch index; --override-arch picks the
# arm64 image out of it. Retried because Docker Hub rate-limits.
n=0
until skopeo copy --retry-times 3 --override-os linux --override-arch arm64 \
	"docker://${REF}" "oci-archive:${ARCHIVE}:docker.io/${SOURCE}:${CHANNEL}"; do
	n=$((n + 1))
	test "${n}" -lt 3 || exit 1
	sleep $((n * 20))
done

# Podman's image ID is the digest of the image config; deriving it here
# lets the service start exactly this image with no tag lookup.
CONFIG_FILE=$(mktemp)
trap 'rm -f "${CONFIG_FILE}"' EXIT HUP INT TERM
skopeo inspect --config --raw "oci-archive:${ARCHIVE}" > "${CONFIG_FILE}"
IMAGE_ID=$(python3 "${REPO_DIR}/tools/check_app_compatibility.py" "${CONFIG_FILE}")
rm -f "${CONFIG_FILE}"
trap - EXIT HUP INT TERM
case "${IMAGE_ID}" in
	[0-9a-f]*) ;;
	*) echo "post-build: could not derive the image ID" >&2; exit 1 ;;
esac

# The OS build's own version. CI passes the version it chose (tools/os_version.py: 0.2, or
# 0.3-dev+abc1234 for a build run by hand); a local build describes itself
# from the version tags, e.g. v0.2-3-gabc1234.
OS_VERSION=${EPILYKOS_OS_VERSION:-$(git -C "${REPO_DIR}" describe --tags --match 'v[0-9]*' --always --dirty 2>/dev/null || echo unknown)}

cat > "${APP_DIR}/image.env" <<-ENV
	# Written at image build time by post-build.sh from manifests/${CHANNEL}.yaml.
	EPILYKOS_CHANNEL=${CHANNEL}
	EPILYKOS_RELEASE=${RELEASE}
	EPILYKOS_SEQUENCE=${SEQUENCE}
	EPILYKOS_IMAGE_REF=${REF}
	EPILYKOS_IMAGE_ID=${IMAGE_ID}
	EPILYKOS_IMAGE_ARCHIVE=/usr/lib/epilykos/epilykos.oci.tar
	EPILYKOS_OS_VERSION=${OS_VERSION}
ENV
echo "post-build: Epilykos image ${IMAGE_ID} preloaded"

# Application updates from Epilykos settings (C-UPDATE-004, D-UPDATE-001)
# accept only manifests signed with the application-release key. Its
# public half is pinned here; without it the box keeps its preloaded image.
PUBKEY="${REPO_DIR}/keys/app-manifest.pub"
if [ -f "${PUBKEY}" ]; then
	install -m 0644 "${PUBKEY}" "${APP_DIR}/app-manifest.pub"
	echo "post-build: app update key pinned"
else
	rm -f "${APP_DIR}/app-manifest.pub"
	echo "post-build: no keys/app-manifest.pub; app updates from settings are off in this image"
fi

# -- Protocol 2: this slot's own Linux (OS updates; development) --
# Each ROOT carries the kernel, DTB and modules of this one build
# (/usr/lib/epilykos/boot, platform.json): U-Boot loads Linux from the slot
# it boots, so an OS update replaces kernel and rootfs together. Image and
# DTB come from this Buildroot run's own output, the modules from this
# target directory; tools/slot_linux.py checks release, architecture and
# board and records a manifest binding every file.
if grep -qx 'BR2_EPILYKOS_BOOT_PROTOCOL_2=y' "${BR2_CONFIG}"; then
	OS_SEQUENCE="${EPILYKOS_OS_SEQUENCE:-}"
	case "${OS_SEQUENCE}" in
		''|0*|*[!0-9]*) echo "post-build: protocol 2 needs EPILYKOS_OS_SEQUENCE (a positive integer)" >&2; exit 1 ;;
	esac
	SOURCE_COMMIT="${EPILYKOS_SOURCE_COMMIT:-$(git -C "${REPO_DIR}" rev-parse HEAD)}"
	set -- "${TARGET_DIR}"/lib/modules/*
	test "$#" -eq 1 && test -d "$1" || { echo "post-build: expected exactly one kernel module release" >&2; exit 1; }
	KERNEL_RELEASE=$(basename "$1")
	PLATFORM_FILE=$(mktemp)
	trap 'rm -f "${PLATFORM_FILE}"' EXIT HUP INT TERM
	python3 - "${PLATFORM_FILE}" "${CHANNEL}" "${OS_VERSION}" "${OS_SEQUENCE}" "${SOURCE_COMMIT}" \
		"${KERNEL_RELEASE}" "${RELEASE}" <<-'PY'
		import json, sys
		path, channel, version, sequence, source, kernel, app = sys.argv[1:]
		platform = {"schema": 1, "board": "raspberrypi3", "arch": "arm64", "slot_layout": "pi3-mbr-v1",
		            "boot_protocol": 2, "channel": channel, "os_version": version, "os_sequence": int(sequence),
		            "source": source, "kernel": kernel, "host_api": 2, "app_release": app}
		# Canonical bytes (sorted, compact, ASCII), as tools/slot_linux.py requires.
		open(path, "w").write(json.dumps(platform, sort_keys=True, separators=(",", ":"), ensure_ascii=True))
	PY
	python3 "${REPO_DIR}/tools/slot_linux.py" --root "${TARGET_DIR}" \
		--image "${BINARIES_DIR}/Image" --dtb "${BINARIES_DIR}/bcm2837-rpi-3-b.dtb" \
		--platform "${PLATFORM_FILE}"
	rm -f "${PLATFORM_FILE}"
	trap - EXIT HUP INT TERM
	# Protocol-2-only units and settings: the every-boot health supervisor
	# instead of epilykos-mark-good, and systemd feeding the watchdog the
	# loader armed.
	cp -R "${BOARD_DIR}/protocol2-overlay/." "${TARGET_DIR}/"
	echo "post-build: protocol 2 slot (OS sequence ${OS_SEQUENCE}, kernel ${KERNEL_RELEASE})"
fi
