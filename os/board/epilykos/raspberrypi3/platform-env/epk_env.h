/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef EPK_ENV_H
#define EPK_ENV_H

#include <stddef.h>
#include <stdint.h>

/* Pi 3B native redundant MMC environment ABI. No extra on-card region. */
#define EPK_ENV_SIZE 16384u
#define EPK_ENV_OFFSET_A 0x400000u
#define EPK_ENV_OFFSET_B 0x404000u
#define EPK_ENV_HEADROOM 2048u
#define EPK_ENV_MAX_UPDATES 64u

enum epk_env_error {
	EPK_ENV_OK = 0,
	EPK_ENV_INVALID = -1,
	EPK_ENV_CONFLICT = -2,
	EPK_ENV_IO = -3,
	EPK_ENV_CAPACITY = -4,
	EPK_ENV_OVERFLOW = -5
};

struct epk_env_selection {
	unsigned int index;
	uint64_t generation;
	uint64_t os_floor;
	uint64_t trust_epoch;
	size_t used;
};

struct epk_env_update {
	const char *name;
	const char *value; /* NULL deletes; empty string stores an empty value. */
};

/* read/write transfer exactly one record. write must flush before returning.
 * The caller holds ONE common exclusive native-env lock across the entire
 * operation (including read/selection). U-Boot is single threaded.
 * Callbacks return 0 on success; failures are never acknowledged as commits.
 */
struct epk_env_io {
	void *context;
	int (*read)(void *, unsigned int index, unsigned char record[EPK_ENV_SIZE]);
	int (*write)(void *, unsigned int index, const unsigned char record[EPK_ENV_SIZE]);
};

/* Strict protocol-2 state only: legacy/unseeded env is not enrolled here.
 * CRC is native little endian, one incremental flag byte, then NUL entries.
 * CRC-valid but undecodable state is an error even if the other copy parses.
 * Signature/policy authorization is the caller's separate mandatory gate.
 */
int epk_env_select(const unsigned char a[EPK_ENV_SIZE],
		   const unsigned char b[EPK_ENV_SIZE], struct epk_env_selection *out);

/* Always replicates, including no-op updates. Critical epk_* changes advance
 * the non-wrapping generation automatically; callers cannot set it directly.
 * Neither floor/epoch nor same-epoch policy may be rolled back or replaced.
 * A successful return means BOTH records were flushed and read back.
 * A failed call can still leave a newer durable, unacknowledged generation:
 * callers must re-read/reconcile, never retry from their stale snapshot.
 */
int epk_env_commit(const struct epk_env_io *io,
		   const struct epk_env_update *updates, size_t count,
		   struct epk_env_selection *out);

#endif
