/* SPDX-License-Identifier: GPL-2.0-or-later */
/* Test-only POSIX backend. Never installed in an image. */
#define _POSIX_C_SOURCE 200809L
#include "epk_env.h"
#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/file.h>
#include <sys/types.h>
#include <unistd.h>

struct backend {
	int fd;
	unsigned int writes;
};

static off_t offset(unsigned int index)
{
	return index ? EPK_ENV_OFFSET_B : EPK_ENV_OFFSET_A;
}

static int read_record(void *ctx, unsigned int index, unsigned char *record)
{
	struct backend *backend = ctx;
	return pread(backend->fd, record, EPK_ENV_SIZE, offset(index)) != EPK_ENV_SIZE;
}

static int fault(const char *name, unsigned int write)
{
	const char *value = getenv(name);
	return value && strtoul(value, NULL, 10) == write;
}

static int write_record(void *ctx, unsigned int index, const unsigned char *record)
{
	struct backend *backend = ctx;
	size_t size = EPK_ENV_SIZE;
	++backend->writes;
	if (fault("EPK_TEST_DROP_WRITE", backend->writes))
		return 0; /* Deliberately lie; readback must catch this. */
	if (fault("EPK_TEST_TORN_WRITE", backend->writes))
		size = 31;
	if (pwrite(backend->fd, record, size, offset(index)) != (ssize_t)size || fsync(backend->fd))
		return -1;
	if (backend->writes == 2 && getenv("EPK_TEST_CORRUPT_EARLIER_COPY")) {
		unsigned char corrupt;
		if (pread(backend->fd, &corrupt, 1, offset(1 - index)) != 1)
			return -1;
		corrupt ^= 1;
		if (pwrite(backend->fd, &corrupt, 1, offset(1 - index)) != 1 || fsync(backend->fd))
			return -1;
	}
	return size != EPK_ENV_SIZE;
}

int main(int argc, char **argv)
{
	struct backend backend;
	struct epk_env_io io = {&backend, read_record, write_record};
	struct epk_env_selection selected;
	struct epk_env_update updates[EPK_ENV_MAX_UPDATES];
	unsigned char records[2][EPK_ENV_SIZE];
	int i, status = EPK_ENV_INVALID;
	if (argc < 3 || argc - 3 > (int)EPK_ENV_MAX_UPDATES)
		return 2;
	backend.fd = open(argv[2], O_RDWR);
	backend.writes = 0;
	if (backend.fd < 0 || flock(backend.fd, LOCK_EX))
		return 2;
	if (!strcmp(argv[1], "select")) {
		if (!read_record(&backend, 0, records[0]) && !read_record(&backend, 1, records[1]))
			status = epk_env_select(records[0], records[1], &selected);
	} else if (!strcmp(argv[1], "commit")) {
		for (i = 3; i < argc; ++i) {
			char *equals = strchr(argv[i], '=');
			updates[i - 3].name = argv[i];
			updates[i - 3].value = equals ? equals + 1 : NULL;
			if (equals) *equals = 0;
		}
		status = epk_env_commit(&io, updates, (size_t)(argc - 3), &selected);
	}
	close(backend.fd);
	if (status) {
		fprintf(stderr, "native env refused: %d\n", status);
		return 1;
	}
	printf("%u %" PRIu64 " %" PRIu64 " %" PRIu64 "\n", selected.index,
	       selected.generation, selected.os_floor, selected.trust_epoch);
	return 0;
}
