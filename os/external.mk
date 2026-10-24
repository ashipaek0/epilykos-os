# EpilykosOS Buildroot external tree.
#
# One shared source, copied into the two pinned native implementations.
# This is loaded after package/boot definitions, extending their recipes.
EPILYKOS_PI3_BOARD = $(BR2_EXTERNAL_EPILYKOS_PATH)/board/epilykos/raspberrypi3

define EPILYKOS_COPY_UBOOT_ENV_GUARD
	test "$(UBOOT_VERSION)" = "2025.10"
	$(INSTALL) -m 0644 $(EPILYKOS_PI3_BOARD)/platform-env/epk_env.h $(@D)/include/epk_env.h
	$(INSTALL) -m 0644 $(EPILYKOS_PI3_BOARD)/platform-env/epk_env.c $(@D)/env/epk_env.c
	$(INSTALL) -m 0644 $(EPILYKOS_PI3_BOARD)/platform-env/epk_mmc.c $(@D)/env/epk_mmc.c
	$(INSTALL) -m 0644 $(EPILYKOS_PI3_BOARD)/platform-env/epk_halt.c $(@D)/env/epk_halt.c
endef
UBOOT_POST_PATCH_HOOKS += EPILYKOS_COPY_UBOOT_ENV_GUARD
UBOOT_PRE_BUILD_HOOKS += EPILYKOS_COPY_UBOOT_ENV_GUARD

define EPILYKOS_COPY_FW_ENV_GUARD
	test "$(UBOOT_TOOLS_VERSION)" = "2026.04"
	$(INSTALL) -m 0644 $(EPILYKOS_PI3_BOARD)/platform-env/epk_env.h $(@D)/tools/env/epk_env.h
	$(INSTALL) -m 0644 $(EPILYKOS_PI3_BOARD)/platform-env/epk_env.c $(@D)/tools/env/epk_env.c
endef
UBOOT_TOOLS_POST_PATCH_HOOKS += EPILYKOS_COPY_FW_ENV_GUARD
HOST_UBOOT_TOOLS_POST_PATCH_HOOKS += EPILYKOS_COPY_FW_ENV_GUARD
UBOOT_TOOLS_PRE_BUILD_HOOKS += EPILYKOS_COPY_FW_ENV_GUARD
HOST_UBOOT_TOOLS_PRE_BUILD_HOOKS += EPILYKOS_COPY_FW_ENV_GUARD

ifeq ($(BR2_EPILYKOS_NATIVE_ENV_GUARD),y)
UBOOT_KCONFIG_FRAGMENT_FILES += $(EPILYKOS_PI3_BOARD)/platform-env/guard.fragment
UBOOT_TOOLS_MAKE_OPTS += EPILYKOS_ENV_GUARD=y
# kconfig-package was evaluated before external.mk. Add the new fragment to
# the concrete stamp prerequisites too, so touching it triggers reconfigure.
ifeq ($(BR2_TARGET_UBOOT),y)
$(UBOOT_DIR)/$(UBOOT_KCONFIG_STAMP_DOTCONFIG): $(EPILYKOS_PI3_BOARD)/platform-env/guard.fragment
endif
endif

# A loader-only manual enable must not leave Linux writers unguarded.
define EPILYKOS_CHECK_NATIVE_ENV_GUARD
	if test "$(BR2_EPILYKOS_NATIVE_ENV_GUARD)" = y; then \
		grep -qx 'CONFIG_EPILYKOS_ENV_GUARD=y' $(@D)/.config; \
	else \
		! grep -qx 'CONFIG_EPILYKOS_ENV_GUARD=y' $(@D)/.config; \
	fi
endef
UBOOT_POST_CONFIGURE_HOOKS += EPILYKOS_CHECK_NATIVE_ENV_GUARD
UBOOT_PRE_BUILD_HOOKS += EPILYKOS_CHECK_NATIVE_ENV_GUARD
