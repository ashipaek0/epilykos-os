# EpilykosOS U-Boot boot script — RAUC bootchooser convention.
# Source for boot.scr; Buildroot compiles this with mkimage
# (BR2_TARGET_UBOOT_BOOT_SCRIPT_SOURCE) and drops boot.scr into the shared
# FAT boot partition alongside u-boot.bin, the kernel and the DTB.
#
# Env vars this depends on (defaults in uboot.env):
#   BOOT_ORDER      space-separated slot names in try order, e.g. "A B"
#   BOOT_A_LEFT     remaining boot attempts for slot A (0 = don't try)
#   BOOT_B_LEFT     remaining boot attempts for slot B
#   active_root     set here to whichever slot is actually being booted;
#                   read by the appliance's boot-health check once the
#                   kernel is up. "rauc status mark-good" (run after that
#                   check passes, C-BOOT-003) resets the matching _LEFT
#                   counter so a good boot stops counting down.
#   pending         set to 1 once both slots are exhausted, so a human or
#                   a recovery target (D-RECOVERY-001 — still open) can
#                   tell "no slot would boot" apart from a normal boot.
#
# This mirrors RAUC's documented U-Boot bootchooser convention (its
# integration guide's U-Boot chapter) but is UNTESTED: no real hardware
# has run it. Proving it actually performs a slot switch and a forced
# fallback — with serial console logs — is C-BOOT-000's evidence
# requirement; this script existing does not by itself satisfy it.

setenv bootargs_common "rootwait rw console=ttyS0,115200 console=tty0"

if itest ${BOOT_A_LEFT} -gt 0; then
	setexpr BOOT_A_LEFT ${BOOT_A_LEFT} - 1
	setenv active_root a
	setenv bootargs root=LABEL=RootFS-A ${bootargs_common}
	saveenv
	fatload mmc 0:1 ${kernel_addr_r} zImage
	fatload mmc 0:1 ${fdt_addr_r} ${fdtfile}
	bootz ${kernel_addr_r} - ${fdt_addr_r}
fi

if itest ${BOOT_B_LEFT} -gt 0; then
	setexpr BOOT_B_LEFT ${BOOT_B_LEFT} - 1
	setenv active_root b
	setenv bootargs root=LABEL=RootFS-B ${bootargs_common}
	saveenv
	fatload mmc 0:1 ${kernel_addr_r} zImage
	fatload mmc 0:1 ${fdt_addr_r} ${fdtfile}
	bootz ${kernel_addr_r} - ${fdt_addr_r}
fi

# Both slots exhausted their tries. There is no recovery target yet
# (D-RECOVERY-001 is still open) — boot slot A one more time rather than
# hanging silently, but flag it so this is visibly not a healthy boot.
echo "EpilykosOS: both slots exhausted boot_tries — booting A without counting it as an attempt. This is a stopgap, not the frozen recovery behaviour."
setenv pending 1
setenv active_root a
setenv bootargs root=LABEL=RootFS-A ${bootargs_common}
saveenv
fatload mmc 0:1 ${kernel_addr_r} zImage
fatload mmc 0:1 ${fdt_addr_r} ${fdtfile}
bootz ${kernel_addr_r} - ${fdt_addr_r}
