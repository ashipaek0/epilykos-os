/* SPDX-License-Identifier: GPL-2.0-or-later */
/* Included by patched env/mmc.c after read_env/write_env are defined.
 * Single-threaded U-Boot backend; cache-aligned buffers are mandatory for DMA.
 */
#include <epk_env.h>

static int epk_mmc_layout(struct mmc *mmc)
{
	u32 a, b;
	return CONFIG_ENV_SIZE == EPK_ENV_SIZE && IS_SD(mmc) &&
		mmc_get_env_dev() == 0 && mmc_get_env_part(mmc) == 0 &&
		!mmc_get_env_addr(mmc, 0, &a) && !mmc_get_env_addr(mmc, 1, &b) &&
		a == EPK_ENV_OFFSET_A && b == EPK_ENV_OFFSET_B;
}

static int epk_mmc_read(void *context, unsigned int index, unsigned char *record)
{
	struct mmc *mmc = context;
	ALLOC_CACHE_ALIGN_BUFFER(env_t, aligned, 1);
	u32 offset;
	if (mmc_get_env_addr(mmc, index, &offset) ||
	    read_env(mmc, EPK_ENV_SIZE, offset, aligned))
		return -1;
	memcpy(record, aligned, EPK_ENV_SIZE);
	return 0;
}

static int epk_mmc_write(void *context, unsigned int index, const unsigned char *record)
{
	struct mmc *mmc = context;
	ALLOC_CACHE_ALIGN_BUFFER(env_t, aligned, 1);
	u32 offset;
	if (mmc_get_env_addr(mmc, index, &offset))
		return -1;
	memcpy(aligned, record, EPK_ENV_SIZE);
	/* SD blk_dwrite waits for programming/busy completion. No eMMC cache is
	 * supported here. Physical power-loss durability remains a Pi test.
	 */
	return write_env(mmc, EPK_ENV_SIZE, offset, aligned);
}

static int epk_mmc_save(struct mmc *mmc, const env_t *desired)
{
	struct epk_env_io io = { mmc, epk_mmc_read, epk_mmc_write };
	struct epk_env_selection selected;
	int result;
	if (!epk_env_is_ready() || !epk_mmc_layout(mmc))
		return 1;
	result = epk_env_commit_snapshot(&io, (const unsigned char *)desired, &selected);
	if (!result) {
		gd->env_valid = selected.index ? ENV_REDUND : ENV_VALID;
		/* Commit advances critical generation; keep the exported hash table
		 * current for a later saveenv in this same loader session.
		 */
		result = env_set_ulong("epk_state_generation", selected.generation);
	}
	if (result) {
		/* A cut/error may already have persisted a newer generation. Never
		 * reuse this RAM snapshot: env load or a fresh boot must reconcile.
		 */
		epk_env_set_ready(0);
		printf("Epilykos env commit refused: %d\n", result);
	}
	return result ? 1 : 0;
}

static int do_epk_env_guard(struct cmd_tbl *cmdtp, int flag, int argc, char *const argv[])
{
	return epk_env_is_ready() ? CMD_RET_SUCCESS : CMD_RET_FAILURE;
}

U_BOOT_CMD(epk_env_guard, 1, 0, do_epk_env_guard,
	   "check imported protocol-2 platform state", "");
