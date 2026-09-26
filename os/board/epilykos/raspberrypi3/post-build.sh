#!/bin/sh
# Buildroot post-build hook (BR2_ROOTFS_POST_BUILD_SCRIPT) — runs against
# the staged target rootfs ($TARGET_DIR) before it's packed into an image.
#
# STATUS: untested scaffold.
set -e

# RAUC needs its data directory even on a read-only-in-intent root; it's
# on DATA in production (mounted at /data by fstab below), not baked into
# either slot.
mkdir -p "${TARGET_DIR}/data"

# fstab: DATA is the only thing every slot mounts read-write. Both ROOT
# slots themselves are ext4 mounted rw for now — C-RUNTIME-002's
# read-only-root tightening is deferred until Stage 2 proves Epilykos has
# no required writes outside mapped paths (see contracts §9).
cat > "${TARGET_DIR}/etc/fstab" <<-EOF
	# <file system>       <mount point>  <type>  <options>              <dump> <pass>
	LABEL=DATA             /data          ext4    defaults,noatime       0      2
	LABEL=BOOT             /boot          vfat    defaults,ro            0      2
EOF

exit 0
