/* SPDX-License-Identifier: GPL-2.0-or-later */
/* Portable shared guard for the protocol-2 loader and native fw-env adapter.
 * No OS calls, allocation, JSON interpretation or cryptographic trust claims.
 */
#include "epk_env.h"
#include <string.h>

#define DATA_SIZE (EPK_ENV_SIZE - 5u)
#define MAX_ENTRIES 256u

struct entry {
	const char *name;
	const char *value;
	size_t name_size;
};

struct view {
	struct entry entries[MAX_ENTRIES];
	size_t count;
	size_t used;
	uint64_t generation;
	uint64_t floor;
	uint64_t epoch;
};

static uint32_t native_crc(const unsigned char *data, size_t size)
{
	uint32_t crc = UINT32_MAX;
	size_t i;
	unsigned int bit;
	for (i = 0; i < size; ++i) {
		crc ^= data[i];
		for (bit = 0; bit < 8; ++bit)
			crc = (crc >> 1) ^ (0xedb88320u & (0u - (crc & 1u)));
	}
	return ~crc;
}

static int crc_valid(const unsigned char *record)
{
	uint32_t stored = (uint32_t)record[0] | ((uint32_t)record[1] << 8) |
		((uint32_t)record[2] << 16) | ((uint32_t)record[3] << 24);
	return stored == native_crc(record + 5, DATA_SIZE);
}

static void set_crc(unsigned char *record)
{
	uint32_t crc = native_crc(record + 5, DATA_SIZE);
	unsigned int i;
	for (i = 0; i < 4; ++i)
		record[i] = (unsigned char)(crc >> (i * 8));
}

static int is_name_char(unsigned char c)
{
	return (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') ||
		(c >= '0' && c <= '9') || c == '_' || c == '.' || c == '-' || c == '#';
}

static int name_equal(const struct entry *entry, const char *name)
{
	return strlen(name) == entry->name_size &&
		!memcmp(entry->name, name, entry->name_size);
}

static const char *get(const struct view *view, const char *name)
{
	size_t i;
	for (i = 0; i < view->count; ++i)
		if (name_equal(&view->entries[i], name))
			return view->entries[i].value;
	return NULL;
}

static int critical(const struct entry *entry)
{
	return entry->name_size >= 4 && !memcmp(entry->name, "epk_", 4);
}

static const char *equivalent(const struct view *view, const struct entry *entry)
{
	size_t i;
	for (i = 0; i < view->count; ++i)
		if (entry->name_size == view->entries[i].name_size &&
		    !memcmp(entry->name, view->entries[i].name, entry->name_size))
			return view->entries[i].value;
	return NULL;
}

static int same_critical(const struct view *a, const struct view *b)
{
	size_t i;
	for (i = 0; i < a->count; ++i) {
		const struct entry *entry = &a->entries[i];
		const char *other;
		if (!critical(entry))
			continue;
		other = equivalent(b, entry);
		if (!other || strcmp(entry->value, other))
			return 0;
	}
	for (i = 0; i < b->count; ++i)
		if (critical(&b->entries[i]) && !equivalent(a, &b->entries[i]))
			return 0;
	return 1;
}

static int decimal(const char *text, uint64_t *out)
{
	uint64_t n = 0;
	if (!text || !*text || (text[0] == '0' && text[1]))
		return 0;
	while (*text) {
		unsigned int digit = (unsigned char)*text++ - '0';
		if (digit > 9 || n > (UINT64_MAX - digit) / 10)
			return 0;
		n = n * 10 + digit;
	}
	*out = n;
	return 1;
}

static int b64_digit(unsigned char c)
{
	if (c >= 'A' && c <= 'Z') return c - 'A';
	if (c >= 'a' && c <= 'z') return c - 'a' + 26;
	if (c >= '0' && c <= '9') return c - '0' + 52;
	if (c == '+') return 62;
	if (c == '/') return 63;
	return -1;
}

/* Canonical padded base64, including unused-bit checks. No decode allocation. */
static size_t b64_size(const char *text)
{
	size_t len, padding = 0, i;
	if (!text || !(len = strlen(text)) || len % 4)
		return 0;
	if (text[len - 1] == '=') ++padding;
	if (text[len - 2] == '=') ++padding;
	for (i = 0; i < len - padding; ++i)
		if (b64_digit((unsigned char)text[i]) < 0)
			return 0;
	if ((padding == 2 && (b64_digit((unsigned char)text[len - 3]) & 15)) ||
	    (padding == 1 && (b64_digit((unsigned char)text[len - 2]) & 3)))
		return 0;
	return len / 4 * 3 - padding;
}

static int parse(const unsigned char *record, struct view *view)
{
	const char *data = (const char *)record + 5;
	size_t position = 0, i, j, policy_size;
	view->count = 0;
	while (position < DATA_SIZE && data[position]) {
		const char *end = memchr(data + position, 0, DATA_SIZE - position);
		const char *equals;
		struct entry *entry;
		if (!end || view->count == MAX_ENTRIES)
			return EPK_ENV_INVALID;
		equals = memchr(data + position, '=', (size_t)(end - data) - position);
		if (!equals || equals == data + position)
			return EPK_ENV_INVALID;
		entry = &view->entries[view->count];
		entry->name = data + position;
		entry->name_size = (size_t)(equals - entry->name);
		entry->value = equals + 1;
		for (i = 0; i < entry->name_size; ++i)
			if (!is_name_char((unsigned char)entry->name[i]))
				return EPK_ENV_INVALID;
		for (i = (size_t)(equals - data) + 1; data + i < end; ++i)
			if ((unsigned char)data[i] < 32 || (unsigned char)data[i] > 126)
				return EPK_ENV_INVALID;
		for (j = 0; j < view->count; ++j)
			if (entry->name_size == view->entries[j].name_size &&
			    !memcmp(entry->name, view->entries[j].name, entry->name_size))
				return EPK_ENV_INVALID;
		++view->count;
		position = (size_t)(end - data) + 1;
	}
	/* One extra NUL follows the final entry's terminating NUL. */
	if (!view->count || position >= DATA_SIZE || data[position])
		return EPK_ENV_INVALID;
	view->used = position + 1;
	if (DATA_SIZE - view->used < EPK_ENV_HEADROOM)
		return EPK_ENV_CAPACITY;
	if (!get(view, "epk_state_schema") || strcmp(get(view, "epk_state_schema"), "1") ||
	    !decimal(get(view, "epk_state_generation"), &view->generation) ||
	    !decimal(get(view, "epk_os_floor"), &view->floor) ||
	    !decimal(get(view, "epk_trust_epoch"), &view->epoch))
		return EPK_ENV_INVALID;
	policy_size = b64_size(get(view, "epk_trust_policy"));
	if (!policy_size || policy_size > 6144 ||
	    b64_size(get(view, "epk_trust_policy_sig")) != 64)
		return EPK_ENV_INVALID;
	return EPK_ENV_OK;
}

static int not_older(const struct view *newer, const struct view *older)
{
	if (newer->floor < older->floor || newer->epoch < older->epoch)
		return 0;
	if (newer->epoch == older->epoch &&
	    (strcmp(get(newer, "epk_trust_policy"), get(older, "epk_trust_policy")) ||
	     strcmp(get(newer, "epk_trust_policy_sig"), get(older, "epk_trust_policy_sig"))))
		return 0;
	return 1;
}

static void selection(struct epk_env_selection *out, const struct view *view, unsigned int index)
{
	out->index = index;
	out->generation = view->generation;
	out->os_floor = view->floor;
	out->trust_epoch = view->epoch;
	out->used = view->used;
}

int epk_env_select(const unsigned char a[EPK_ENV_SIZE],
		   const unsigned char b[EPK_ENV_SIZE], struct epk_env_selection *out)
{
	const unsigned char *records[2] = {a, b};
	struct view views[2];
	int valid[2], status;
	unsigned int i, chosen;
	if (!a || !b || !out)
		return EPK_ENV_INVALID;
	for (i = 0; i < 2; ++i) {
		valid[i] = crc_valid(records[i]);
		if (valid[i] && (status = parse(records[i], &views[i])) != EPK_ENV_OK)
			return status;
	}
	if (!valid[0] && !valid[1])
		return EPK_ENV_INVALID;
	if (!valid[0] || !valid[1]) {
		chosen = valid[0] ? 0 : 1;
	} else if (views[0].generation != views[1].generation) {
		chosen = views[1].generation > views[0].generation ? 1 : 0;
		if (!not_older(&views[chosen], &views[1 - chosen]))
			return EPK_ENV_CONFLICT;
	} else {
		if (!same_critical(&views[0], &views[1]))
			return EPK_ENV_CONFLICT;
		/* U-Boot MMC's incremental active flag, including 255 -> 0. */
		if (a[4] == 255 && b[4] == 0) chosen = 1;
		else if (a[4] == 0 && b[4] == 255) chosen = 0;
		else chosen = b[4] > a[4] ? 1 : 0;
	}
	selection(out, &views[chosen], chosen);
	return EPK_ENV_OK;
}

static int append(unsigned char *out, size_t *used, const char *name, size_t name_size, const char *value)
{
	size_t value_size = strlen(value);
	if (name_size + value_size + 3 > DATA_SIZE - *used)
		return EPK_ENV_CAPACITY;
	memcpy(out + 5 + *used, name, name_size);
	*used += name_size;
	out[5 + (*used)++] = '=';
	memcpy(out + 5 + *used, value, value_size + 1);
	*used += value_size + 1;
	out[5 + *used] = 0;
	return EPK_ENV_OK;
}

static void to_decimal(uint64_t value, char out[21])
{
	char reverse[20];
	size_t n = 0, i;
	do {
		reverse[n++] = (char)('0' + value % 10);
		value /= 10;
	} while (value);
	for (i = 0; i < n; ++i)
		out[i] = reverse[n - 1 - i];
	out[n] = 0;
}

static int changed_critical(const struct view *old, const struct epk_env_update *updates, size_t count)
{
	size_t i;
	for (i = 0; i < count; ++i) {
		const char *previous;
		if (strncmp(updates[i].name, "epk_", 4))
			continue;
		previous = get(old, updates[i].name);
		if ((!previous != !updates[i].value) ||
		    (previous && strcmp(previous, updates[i].value)))
			return 1;
	}
	return 0;
}

static int build(const unsigned char *source, const struct epk_env_update *updates,
		 size_t count, unsigned char *out)
{
	struct view old, next;
	size_t i, j, used = 0;
	int status, changed;
	char generation[21];
	status = parse(source, &old);
	if (status) return status;
	for (i = 0; i < count; ++i) {
		if (!updates[i].name || !*updates[i].name ||
		    !strcmp(updates[i].name, "epk_state_generation"))
			return EPK_ENV_INVALID;
		for (j = 0; j < i; ++j)
			if (!strcmp(updates[j].name, updates[i].name))
				return EPK_ENV_INVALID;
	}
	changed = changed_critical(&old, updates, count);
	if (changed && old.generation == UINT64_MAX)
		return EPK_ENV_OVERFLOW;
	to_decimal(old.generation + (unsigned int)changed, generation);
	memset(out, 0, EPK_ENV_SIZE);
	for (i = 0; i < old.count; ++i) {
		const struct entry *entry = &old.entries[i];
		const char *value = entry->value;
		for (j = 0; j < count; ++j)
			if (name_equal(entry, updates[j].name)) {
				value = updates[j].value;
				break;
			}
		if (name_equal(entry, "epk_state_generation"))
			value = generation;
		if (value && (status = append(out, &used, entry->name, entry->name_size, value)))
			return status;
	}
	for (j = 0; j < count; ++j)
		if (!get(&old, updates[j].name) && updates[j].value &&
		    (status = append(out, &used, updates[j].name, strlen(updates[j].name), updates[j].value)))
			return status;
	status = parse(out, &next);
	if (status) return status;
	if (!not_older(&next, &old))
		return EPK_ENV_CONFLICT;
	set_crc(out);
	return EPK_ENV_OK;
}

int epk_env_commit(const struct epk_env_io *io, const struct epk_env_update *updates,
		   size_t count, struct epk_env_selection *out)
{
	unsigned char records[2][EPK_ENV_SIZE], next[EPK_ENV_SIZE];
	struct epk_env_selection chosen;
	unsigned int pass, index;
	unsigned char flag;
	int status;
	if (!io || !io->read || !io->write || !out || count > EPK_ENV_MAX_UPDATES ||
	    (count && !updates))
		return EPK_ENV_INVALID;
	if (io->read(io->context, 0, records[0]) || io->read(io->context, 1, records[1]))
		return EPK_ENV_IO;
	status = epk_env_select(records[0], records[1], &chosen);
	if (status) return status;
	status = build(records[chosen.index], updates, count, next);
	if (status) return status;
	flag = records[chosen.index][4];
	/* Preserve the selected record until the other record is durable. Always
	 * perform both writes: a native dirty/no-op shortcut is not replication.
	 */
	for (pass = 0; pass < 2; ++pass) {
		index = pass ? chosen.index : 1 - chosen.index;
		next[4] = ++flag;
		if (io->write(io->context, index, next) ||
		    io->read(io->context, index, records[index]) ||
		    memcmp(records[index], next, EPK_ENV_SIZE))
			return EPK_ENV_IO;
	}
	/* Re-read BOTH, not only the last write. A backend may misaddress writes. */
	if (io->read(io->context, 0, records[0]) || io->read(io->context, 1, records[1]))
		return EPK_ENV_IO;
	if (!crc_valid(records[0]) || !crc_valid(records[1]) ||
	    records[chosen.index][4] != flag ||
	    records[1 - chosen.index][4] != (unsigned char)(flag - 1) ||
	    memcmp(records[0] + 5, next + 5, DATA_SIZE) ||
	    memcmp(records[1] + 5, next + 5, DATA_SIZE))
		return EPK_ENV_IO;
	return epk_env_select(records[0], records[1], out);
}
