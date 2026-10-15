# EpilykosOS U-Boot boot script — RAUC bootchooser convention.
# Compiled to boot.scr by host-uboot-tools (mkimage); U-Boot's standard
# boot finds it on the FAT BOOT partition and runs it.
#
# Slot selection follows RAUC's reference U-Boot script (rauc
# contrib/uboot.sh) so RAUC's uboot backend and this script agree:
#   BOOT_ORDER      slot names in try order, e.g. "A B" (RAUC rewrites it
#                   when an update makes the other slot primary)
#   BOOT_A_LEFT     remaining boot attempts for slot A
#   BOOT_B_LEFT     remaining boot attempts for slot B
# Every boot decrements the chosen slot's counter before starting Linux;
# `rauc status mark-good` (epilykos-mark-good.service, C-BOOT-003) resets
# it to boot-attempts once the boot is healthy. A slot whose counter hits
# 0 is skipped, which is the automatic fallback.
#
# Root is selected by PARTUUID, which the kernel resolves without an
# initramfs. The MBR disk signature 0x45504c4b is fixed in genimage.cfg,
# so the IDs are 45504c4b-02 (ROOT-A) and 45504c4b-03 (ROOT-B).
#
# The environment lives raw on the SD card (uboot.fragment). On first
# boot it is blank, so the variables below get their defaults and the
# first saveenv writes them.

test -n "${BOOT_ORDER}" || setenv BOOT_ORDER "A B"
test -n "${BOOT_A_LEFT}" || setenv BOOT_A_LEFT 3
test -n "${BOOT_B_LEFT}" || setenv BOOT_B_LEFT 3

# Standard boot sets devtype/devnum for the device this script came from.
test -n "${devtype}" || setenv devtype mmc
test -n "${devnum}" || setenv devnum 0

setenv bootargs
for BOOT_SLOT in "${BOOT_ORDER}"; do
  if test "x${bootargs}" != "x"; then
    # a slot was already chosen, skip the rest
  elif test "x${BOOT_SLOT}" = "xA"; then
    if test 0x${BOOT_A_LEFT} -gt 0; then
      echo "EpilykosOS: booting slot A, ${BOOT_A_LEFT} attempts left before this one"
      setexpr BOOT_A_LEFT ${BOOT_A_LEFT} - 1
      setenv bootargs "root=PARTUUID=45504c4b-02 rauc.slot=A"
    fi
  elif test "x${BOOT_SLOT}" = "xB"; then
    if test 0x${BOOT_B_LEFT} -gt 0; then
      echo "EpilykosOS: booting slot B, ${BOOT_B_LEFT} attempts left before this one"
      setexpr BOOT_B_LEFT ${BOOT_B_LEFT} - 1
      setenv bootargs "root=PARTUUID=45504c4b-03 rauc.slot=B"
    fi
  fi
done

if test -n "${bootargs}"; then
  saveenv
else
  # Both slots used up their attempts. There is no recovery target yet
  # (D-RECOVERY-001 is open); this is RAUC's reference behaviour:
  # give both slots their attempts back and start over.
  echo "EpilykosOS: no slot has attempts left; resetting both to 3"
  setenv BOOT_A_LEFT 3
  setenv BOOT_B_LEFT 3
  saveenv
  reset
fi

# Serial console last so it is /dev/console (systemd's status output and
# the boot evidence for C-BOOT-000 go there); HDMI (tty1) gets a copy.
setenv bootargs "${bootargs} rootfstype=ext4 rootwait rw console=tty1 console=ttyS1,115200"

fatload ${devtype} ${devnum}:1 ${kernel_addr_r} Image
fatload ${devtype} ${devnum}:1 ${fdt_addr_r} bcm2837-rpi-3-b.dtb
booti ${kernel_addr_r} - ${fdt_addr_r}

echo "EpilykosOS: booti returned; kernel or device tree failed to load"
