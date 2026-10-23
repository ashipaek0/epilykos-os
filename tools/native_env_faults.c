/* SPDX-License-Identifier: GPL-2.0-or-later */
/* LD_PRELOAD fault injector for real native fw-env integration tests only.
 * Never copied into U-Boot, a target rootfs or a production signing workflow.
 */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <stdlib.h>
#include <sys/types.h>
#include <unistd.h>

static unsigned int writes;
static int env_fd = -1;

static int at_write(const char *name)
{
	const char *value = getenv(name);
	return value && strtoul(value, NULL, 10) == writes;
}

ssize_t pwrite(int fd, const void *buf, size_t count, off_t offset)
{
	ssize_t (*real_pwrite)(int, const void *, size_t, off_t) = dlsym(RTLD_NEXT, "pwrite");
	if (count == 16384 && (offset == 0x400000 || offset == 0x404000)) {
		++writes;
		env_fd = fd;
		if (at_write("EPK_TEST_DROP_WRITE"))
			return (ssize_t)count;
		if (at_write("EPK_TEST_TORN_WRITE"))
			return real_pwrite(fd, buf, 31, offset);
	}
	return real_pwrite(fd, buf, count, offset);
}

int fsync(int fd)
{
	int (*real_fsync)(int) = dlsym(RTLD_NEXT, "fsync");
	int result = real_fsync(fd);
	if (fd == env_fd && at_write("EPK_TEST_FAIL_FSYNC")) {
		errno = EIO;
		return -1;
	}
	return result;
}
