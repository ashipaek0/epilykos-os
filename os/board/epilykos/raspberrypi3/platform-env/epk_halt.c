/* SPDX-License-Identifier: GPL-2.0-or-later */
/* Terminal endpoint: do not return to bootflow or refill exhausted slots. */
#include <command.h>
#include <env.h>
#include <hang.h>
#include <stdio.h>
#include <linux/string.h>
#if CONFIG_IS_ENABLED(WDT)
#include <dm.h>
#include <dm/uclass.h>
#include <wdt.h>
#endif

static int lower_hex(char c)
{
	return (c >= '0' && c <= '9') || (c >= 'a' && c <= 'f');
}

static int job_uuid(const char *value)
{
	unsigned int i;

	if (!value || strlen(value) != 36 || value[14] != '4' ||
	    !strchr("89ab", value[19]))
		return 0;
	for (i = 0; i < 36; ++i) {
		if (i == 8 || i == 13 || i == 18 || i == 23) {
			if (value[i] != '-')
				return 0;
		} else if (!lower_hex(value[i])) {
			return 0;
		}
	}
	return 1;
}

static int positive_sequence(const char *value)
{
	size_t i, length;

	if (!value || value[0] < '1' || value[0] > '9')
		return 0;
	length = strlen(value);
	if (length > 20 || (length == 20 &&
	    strcmp(value, "18446744073709551615") > 0))
		return 0;
	for (i = 1; i < length; ++i)
		if (value[i] < '0' || value[i] > '9')
			return 0;
	return 1;
}

static int do_epk_boot_guard(struct cmd_tbl *cmdtp, int flag, int argc,
			     char *const argv[])
{
	const char *pending = env_get("epk_os_pending");
	const char *hash;
	unsigned int i;

	/* Slot/phase/marker consistency is checked by the fixed hush script.
	 * Check bounded identity syntax here before that script can save state.
	 */
	if (!pending || !*pending)
		return CMD_RET_SUCCESS;
	hash = env_get("epk_os_target_sha256");
	if (!job_uuid(pending) ||
	    !positive_sequence(env_get("epk_os_target_sequence")) ||
	    !hash || strlen(hash) != 64)
		return CMD_RET_FAILURE;
	for (i = 0; i < 64; ++i)
		if (!lower_hex(hash[i]))
			return CMD_RET_FAILURE;
	return CMD_RET_SUCCESS;
}

U_BOOT_CMD(epk_boot_guard, 1, 0, do_epk_boot_guard,
	   "check Epilykos pending boot identity syntax", "");

static int do_epk_boot_halt(struct cmd_tbl *cmdtp, int flag, int argc,
			  char *const argv[])
{
#if CONFIG_IS_ENABLED(WDT)
	struct udevice *dev;
	int result;

	for (uclass_first_device(UCLASS_WDT, &dev); dev;
	     uclass_next_device(&dev)) {
		result = wdt_stop(dev);
		if (result)
			printf("EpilykosOS: watchdog stop failed: %d\n", result);
	}
#endif
	puts("EpilykosOS: diagnostic halt; spare-card repair required\n");
	hang();
	return CMD_RET_FAILURE;
}

U_BOOT_CMD(epk_boot_halt, 1, 0, do_epk_boot_halt,
	   "terminal Epilykos slot diagnostic halt", "");
