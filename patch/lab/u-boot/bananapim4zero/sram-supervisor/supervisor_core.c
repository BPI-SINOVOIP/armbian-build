/* SPDX-License-Identifier: GPL-2.0+ */
#include "supervisor.h"

static u32 read_le32(const u8 *p)
{
	return (u32)p[0] | (u32)p[1] << 8 | (u32)p[2] << 16 |
	       (u32)p[3] << 24;
}

int sup_padding(const u8 *data, u32 bytes)
{
	u32 i;

	for (i = 0; i < bytes; ++i)
		if (data[i])
			return -1;
	return 0;
}

int sup_header(const u8 *header, struct sup_image *out)
{
	static const u8 magic[8] = {'B', 'P', 'I', 'S', 'R', 'A', 'M', '1'};
	u32 i, image_bytes, runtime_bytes;

	for (i = 0; i < sizeof(magic); ++i)
		if (header[i] != magic[i])
			return -1;
	if (read_le32(header + 8) != 1 ||
	    read_le32(header + 12) != SUP_HEADER_BYTES ||
	    read_le32(header + 16) != SUP_BOARD_ID ||
	    read_le32(header + 28) != SUP_LOAD_BASE ||
	    read_le32(header + 32) != 0 || read_le32(header + 36) != 1 ||
	    sup_padding(header + 72, 436) ||
	    sup_crc32(header, 508) != read_le32(header + 508))
		return -1;
	image_bytes = read_le32(header + 20);
	runtime_bytes = read_le32(header + 24);
	if (!image_bytes || image_bytes > SUP_IMAGE_MAX ||
	    runtime_bytes < image_bytes || runtime_bytes > SUP_RUNTIME_MAX ||
	    (runtime_bytes & 15))
		return -1;
	out->image_bytes = image_bytes;
	out->runtime_bytes = runtime_bytes;
	out->package_bytes = SUP_HEADER_BYTES + (image_bytes / 512 + 1) * 512;
	out->kind = 1;
	for (i = 0; i < 32; ++i)
		out->digest[i] = header[40 + i];
	return 0;
}

int sup_decimal(const char *text, u32 limit, u32 *out)
{
	u32 value = 0, digit;

	if (!text || !*text)
		return -1;
	while (*text) {
		if (*text < '0' || *text > '9')
			return -1;
		digit = *text++ - '0';
		if (digit > limit || value > (limit - digit) / 10)
			return -1;
		value = value * 10 + digit;
	}
	*out = value;
	return 0;
}

int sup_slot(u32 index, u32 *lba)
{
	if (index >= SUP_SLOT_COUNT)
		return -1;
	*lba = SUP_FIRST_SLOT_LBA + index * SUP_SLOT_STRIDE_LBA;
	return 0;
}
