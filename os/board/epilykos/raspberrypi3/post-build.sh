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
IMAGE_ID=$(skopeo inspect --config --raw "oci-archive:${ARCHIVE}" | sha256sum | cut -d' ' -f1)
case "${IMAGE_ID}" in
	[0-9a-f]*) ;;
	*) echo "post-build: could not derive the image ID" >&2; exit 1 ;;
esac

# The OS build's own version: the commit this image was built from.
OS_VERSION=$(git -C "${REPO_DIR}" describe --always --dirty 2>/dev/null || echo unknown)

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
