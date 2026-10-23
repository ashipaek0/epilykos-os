# Experimental protocol 2. Compile only with guarded, seeded MMC bootstrap.
# Fixed card/partitions match the native env ABI; never follow a USB bootflow
# device into a different ROOT. No fallback to a shared BOOT kernel/DTB.
setenv epb_halt 'epk_boot_halt; while true; do sleep 1; done'
setenv epb_reset 'reset; run epb_halt'

if epk_env_guard && epk_boot_guard; then
  true
else
  echo "EpilykosOS: native state unavailable"
  run epb_halt
fi

if test "${BOOT_ORDER}" = "A B" || test "${BOOT_ORDER}" = "B A" || test "${BOOT_ORDER}" = "A" || test "${BOOT_ORDER}" = "B"; then
  true
else
  echo "EpilykosOS: invalid boot order"
  run epb_halt
fi
if test "${BOOT_A_LEFT}" = 0 || test "${BOOT_A_LEFT}" = 1 || test "${BOOT_A_LEFT}" = 2 || test "${BOOT_A_LEFT}" = 3; then
  true
else
  echo "EpilykosOS: invalid A counter"
  run epb_halt
fi
if test "${BOOT_B_LEFT}" = 0 || test "${BOOT_B_LEFT}" = 1 || test "${BOOT_B_LEFT}" = 2 || test "${BOOT_B_LEFT}" = 3; then
  true
else
  echo "EpilykosOS: invalid B counter"
  run epb_halt
fi

if test -n "${epk_os_pending}"; then
  if test "${epk_os_target}" = A && test "${epk_os_from}" = B; then
    true
  elif test "${epk_os_target}" = B && test "${epk_os_from}" = A; then
    true
  else
    echo "EpilykosOS: invalid pending slots"
    run epb_halt
  fi
  if test -z "${epk_os_target_sequence}" || test -z "${epk_os_target_sha256}"; then
    echo "EpilykosOS: incomplete pending identity"
    run epb_halt
  fi
  if test "${epk_os_phase}" = ready || test "${epk_os_phase}" = activation_intent || test "${epk_os_phase}" = activated || test "${epk_os_phase}" = trying || test "${epk_os_phase}" = confirming || test "${epk_os_phase}" = promoting; then
    true
  else
    echo "EpilykosOS: unknown pending phase"
    run epb_halt
  fi
  if test -n "${epk_os_trial_started}" && test "${epk_os_trial_started}" != "${epk_os_pending}"; then
    echo "EpilykosOS: conflicting trial job"
    run epb_halt
  fi
  if test "${epk_os_phase}" = ready && test -n "${epk_os_trial_started}"; then
    echo "EpilykosOS: trial marker on unactivated job"
    run epb_halt
  fi
elif test -n "${epk_os_phase}" || test -n "${epk_os_trial_started}" || test -n "${epk_os_target}" || test -n "${epk_os_from}" || test -n "${epk_os_target_sequence}" || test -n "${epk_os_target_sha256}"; then
  echo "EpilykosOS: orphaned pending state"
  run epb_halt
fi

setenv epb_slot
setenv epb_part
for BOOT_SLOT in ${BOOT_ORDER}; do
  if test -z "${epb_slot}"; then
    if test -n "${epk_os_pending}" && test "${epk_os_phase}" = ready && test "${BOOT_SLOT}" = "${epk_os_target}"; then
      echo "EpilykosOS: ready target remains non-bootable"
    elif test "${BOOT_SLOT}" = A && test "${BOOT_A_LEFT}" != 0; then
      setenv epb_slot A
      setenv epb_part 2
    elif test "${BOOT_SLOT}" = B && test "${BOOT_B_LEFT}" != 0; then
      setenv epb_slot B
      setenv epb_part 3
    fi
  fi
done
if test -z "${epb_slot}"; then
  echo "EpilykosOS: no eligible slot; counters retained"
  run epb_halt
fi

if test "${epb_slot}" = A; then
  if setexpr BOOT_A_LEFT ${BOOT_A_LEFT} - 1; then true; else run epb_halt; fi
else
  if setexpr BOOT_B_LEFT ${BOOT_B_LEFT} - 1; then true; else run epb_halt; fi
fi
# This marker shares the counter save. Never overwrite confirmation intent.
if test -n "${epk_os_pending}" && test "${epb_slot}" = "${epk_os_target}" && test "${epk_os_trial_started}" != "${epk_os_pending}"; then
  setenv epk_os_trial_started "${epk_os_pending}"
fi
if saveenv; then
  true
else
  echo "EpilykosOS: attempt/intent save failed; refusing launch"
  run epb_halt
fi

setenv bootargs "root=PARTUUID=45504c4b-0${epb_part} rauc.slot=${epb_slot} rootfstype=ext4 rootwait ro fsck.repair=yes"
if test -n "${machine_id}"; then
  setenv bootargs "${bootargs} systemd.machine_id=${machine_id}"
fi
setenv bootargs "${bootargs} console=tty1 console=ttyS1,115200"
if ext4load mmc 0:${epb_part} ${kernel_addr_r} /usr/lib/epilykos/boot/Image; then
  if ext4load mmc 0:${epb_part} ${fdt_addr_r} /usr/lib/epilykos/boot/bcm2837-rpi-3-b.dtb; then
    booti ${kernel_addr_r} - ${fdt_addr_r}
  fi
fi
echo "EpilykosOS: slot load/boot failed; attempt remains spent"
run epb_reset
