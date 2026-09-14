/* SPDX-License-Identifier: GPL-2.0+ */
#include "supervisor.h"
#include <zlib.h>

u32 sup_crc32(const u8 *data, u32 bytes)
{
	return (u32)crc32(0L, data, bytes);
}

size_t sup_test_image_size(void)
{
	return sizeof(struct sup_image);
}

#ifdef SUP_TEST_MAIN
#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static unsigned long checks;
static const char *phase;
static u32 random_state = 0x20260915U;

static void check(int condition, int line)
{
	++checks;
	if (!condition) {
		fprintf(stderr, "測試失敗：%s，第 %d 行，第 %lu 次檢查\n",
			phase, line, checks);
		exit(EXIT_FAILURE);
	}
}

#define CHECK(condition) check((condition), __LINE__)

static u32 random_u32(void)
{
	random_state ^= random_state << 13;
	random_state ^= random_state >> 17;
	random_state ^= random_state << 5;
	return random_state;
}

static u32 get32(const u8 *p)
{
	u32 value = 0;
	unsigned int i;

	for (i = 0; i < 4; ++i)
		value |= (u32)p[i] << (8 * i);
	return value;
}

static void put32(u8 *p, u32 value)
{
	unsigned int i;

	for (i = 0; i < 4; ++i)
		p[i] = (u8)(value >> (8 * i));
}

static void seal(u8 *header)
{
	put32(header + 508, (u32)crc32(0L, header, 508));
}

static void valid_header(u8 *header)
{
	unsigned int i;

	memset(header, 0, 512);
	memcpy(header, "BPISRAM1", 8);
	put32(header + 8, 1);
	put32(header + 12, 512);
	put32(header + 16, 0x06180001U);
	put32(header + 20, 513);
	put32(header + 24, 1024);
	put32(header + 28, 0x30000);
	put32(header + 36, 1);
	for (i = 0; i < 32; ++i)
		header[40 + i] = (u8)i;
	seal(header);
}

/* 固定 ABI 的獨立判準；不呼叫待測核心來產生預期結果。 */
static int expected_header(const u8 *header)
{
	static const u32 fields[][2] = {
		{8, 1}, {12, 512}, {16, 0x06180001U},
		{28, 0x30000}, {32, 0}, {36, 1},
	};
	uint64_t image = get32(header + 20);
	uint64_t runtime = get32(header + 24);
	unsigned int i;

	if (memcmp(header, "BPISRAM1", 8))
		return -1;
	for (i = 0; i < sizeof(fields) / sizeof(fields[0]); ++i)
		if (get32(header + fields[i][0]) != fields[i][1])
			return -1;
	for (i = 72; i < 508; ++i)
		if (header[i] != 0)
			return -1;
	if ((u32)crc32(0L, header, 508) != get32(header + 508))
		return -1;
	return image >= 1 && image <= 97792 && runtime >= image &&
		runtime <= 98304 && runtime % 16 == 0 ? 0 : -1;
}

static void check_header(const u8 *header, int expected)
{
	struct guarded_image {
		u8 before[16];
		struct sup_image image;
		u8 after[16];
	} out, original;
	u8 snapshot[512];
	u32 image_bytes = get32(header + 20);
	int result;

	memset(&out, 0xa5, sizeof(out));
	memcpy(&original, &out, sizeof(out));
	memcpy(snapshot, header, sizeof(snapshot));
	result = sup_header(header, &out.image);
	CHECK(result == expected);
	CHECK(memcmp(header, snapshot, sizeof(snapshot)) == 0);
	CHECK(memcmp(out.before, original.before, sizeof(out.before)) == 0);
	CHECK(memcmp(out.after, original.after, sizeof(out.after)) == 0);
	if (expected != 0) {
		CHECK(memcmp(&out, &original, sizeof(out)) == 0);
		return;
	}
	CHECK(out.image.image_bytes == image_bytes);
	CHECK(out.image.runtime_bytes == get32(header + 24));
	CHECK(out.image.package_bytes == 512 + image_bytes + 512 - image_bytes % 512);
	CHECK(out.image.kind == 1);
	CHECK(memcmp(out.image.digest, header + 40, 32) == 0);
}

static void test_headers(u8 *header)
{
	static const u32 boundaries[] = {
		0, 1, 15, 16, 17, 31, 32, 511, 512, 513,
		97791, 97792, 97793, 98288, 98303, 98304, 98305,
		UINT32_MAX - 15, UINT32_MAX,
	};
	u8 baseline[512];
	unsigned int i, j, byte, bit;

	phase = "標頭邊界與逐位元變異";
	valid_header(baseline);
	memcpy(header, baseline, 512);
	check_header(header, 0);
	for (i = 0; i < 512; ++i) {
		for (bit = 0; bit < 8; ++bit) {
			memcpy(header, baseline, 512);
			header[i] ^= (u8)(1U << bit);
			check_header(header, -1);
			if (i < 508) {
				seal(header);
				check_header(header, expected_header(header));
			}
		}
	}
	phase = "標頭逐位元組全值遍歷";
	for (i = 0; i < 512; ++i) {
		for (byte = 0; byte < 256; ++byte) {
			if (byte == baseline[i])
				continue;
			memcpy(header, baseline, 512);
			header[i] = (u8)byte;
			check_header(header, -1);
			if (i < 508) {
				seal(header);
				check_header(header, expected_header(header));
			}
		}
	}
	phase = "映像與執行期大小交叉邊界";
	for (i = 0; i < sizeof(boundaries) / sizeof(boundaries[0]); ++i) {
		for (j = 0; j < sizeof(boundaries) / sizeof(boundaries[0]); ++j) {
			valid_header(header);
			put32(header + 20, boundaries[i]);
			put32(header + 24, boundaries[j]);
			seal(header);
			check_header(header, expected_header(header));
		}
	}
}

static void test_padding(u8 *data)
{
	unsigned int size, i, value;

	phase = "零填補長度與逐位元組遍歷";
	for (size = 0; size <= 512; ++size) {
		memset(data, 0, 512);
		CHECK(sup_padding(data, size) == 0);
		if (size < 512) {
			data[size] = 0xff;
			CHECK(sup_padding(data, size) == 0);
		}
	}
	memset(data, 0, 512);
	for (i = 0; i < 512; ++i) {
		for (value = 1; value <= 255; ++value) {
			data[i] = (u8)value;
			CHECK(sup_padding(data, 512) == -1);
			CHECK(data[i] == value);
		}
		data[i] = 0;
	}
}

static void check_decimal(const char *text, u32 limit, int expected, u32 value)
{
	u32 out[3] = {0x12345678U, 0xa5a5a5a5U, 0x87654321U};

	CHECK(sup_decimal(text, limit, &out[1]) == expected);
	CHECK(out[0] == 0x12345678U && out[2] == 0x87654321U);
	CHECK(out[1] == (expected == 0 ? value : 0xa5a5a5a5U));
}

static void test_decimal_slots(void)
{
	static const char *invalid[] = {
		NULL, "", " ", "+1", "-1", "1 ", " 1", "1\n", "1\t",
		"0x1", "1.0", "1e2", "4294967296", "999999999999999999999999",
	};
	static const u32 bad_slots[] = {5, 6, 65535, 0x80000000U, UINT32_MAX};
	unsigned int i, byte;
	char text[4] = "123";
	u32 out;

	phase = "十進位與槽索引邊界";
	check_decimal("0", 0, 0, 0);
	check_decimal("0000", 0, 0, 0);
	check_decimal("1", 0, -1, 0);
	check_decimal("9", 8, -1, 0);
	check_decimal("10", 9, -1, 0);
	check_decimal("100", 99, -1, 0);
	check_decimal("4294967295", UINT32_MAX, 0, UINT32_MAX);
	check_decimal("4294967295", UINT32_MAX - 1, -1, 0);
	for (i = 0; i < sizeof(invalid) / sizeof(invalid[0]); ++i)
		check_decimal(invalid[i], UINT32_MAX, -1, 0);
	for (i = 0; i < 3; ++i) {
		for (byte = 1; byte <= 255; ++byte) {
			memcpy(text, "123", 4);
			text[i] = (char)byte;
			if (byte >= '0' && byte <= '9') {
				u32 value = (u32)(text[0] - '0') * 100 +
					(u32)(text[1] - '0') * 10 + (u32)(text[2] - '0');
				check_decimal(text, 999, 0, value);
			} else {
				check_decimal(text, UINT32_MAX, -1, 0);
			}
		}
	}
	for (i = 0; i < 5; ++i) {
		out = 0xa5a5a5a5U;
		CHECK(sup_slot(i, &out) == 0);
		CHECK(out == 6144U + i * 2048U);
	}
	for (i = 0; i < sizeof(bad_slots) / sizeof(bad_slots[0]); ++i) {
		out = 0xa5a5a5a5U;
		CHECK(sup_slot(bad_slots[i], &out) == -1);
		CHECK(out == 0xa5a5a5a5U);
	}
}

static void test_random(u8 *header)
{
	u32 iteration, i, value, limit, index, out;
	char decimal[32];

	phase = "固定種子隨機標頭與整數壓力測試";
	for (iteration = 0; iteration < 20000; ++iteration) {
		valid_header(header);
		switch (iteration % 4) {
		case 0:
			for (i = 0; i < 512; ++i)
				header[i] = (u8)random_u32();
			break;
		case 1:
			value = 1 + random_u32() % 97792;
			put32(header + 20, value);
			put32(header + 24, (value + 15) & ~15U);
			for (i = 40; i < 72; ++i)
				header[i] = (u8)random_u32();
			seal(header);
			break;
		case 2:
			i = 8 + 4 * (random_u32() % 8);
			value = random_u32();
			put32(header + i, value);
			seal(header);
			break;
		default:
			put32(header + 20, random_u32() % 100000);
			put32(header + 24, random_u32() % 100000);
			seal(header);
			break;
		}
		check_header(header, expected_header(header));
		value = random_u32();
		limit = random_u32();
		CHECK(snprintf(decimal, sizeof(decimal), "%" PRIu32, value) > 0);
		check_decimal(decimal, limit, value <= limit ? 0 : -1, value);
		CHECK(snprintf(decimal, sizeof(decimal), "%" PRIu64,
			(uint64_t)UINT32_MAX + 1 + value) > 0);
		check_decimal(decimal, UINT32_MAX, -1, 0);
		index = random_u32();
		out = 0xa5a5a5a5U;
		CHECK(sup_slot(index, &out) == (index < 5 ? 0 : -1));
		CHECK(out == (index < 5 ? 6144U + index * 2048U : 0xa5a5a5a5U));
	}
}

int main(void)
{
	u8 *header;

	phase = "測試資源與 ABI";
	CHECK(SUP_HEADER_BYTES == 512 && sizeof(struct sup_image) == 48);
	CHECK(SUP_IMAGE_MAX == 97792 && SUP_RUNTIME_MAX == 98304);
	header = malloc(512);
	CHECK(header != NULL);
	test_headers(header);
	test_padding(header);
	test_decimal_slots();
	test_random(header);
	free(header);
	printf("C 核心壓力測試通過：%lu 次檢查，固定種子 0x20260915，隨機標頭 20000 筆\n",
	       checks);
	return EXIT_SUCCESS;
}
#endif
