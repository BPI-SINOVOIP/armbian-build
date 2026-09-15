/* SPDX-License-Identifier: GPL-2.0+ */
#include <common.h>
#include <mmc.h>
#include <serial.h>
#include <xyzModem.h>
#include <u-boot/crc.h>
#include <u-boot/sha256.h>
#include "supervisor.h"

static u8 header[512] __aligned(16);
static u8 buffer[1024] __aligned(16);
static u8 check_buffer[1024] __aligned(16);
static u8 zero_sector[512] __aligned(16);
static u8 original_mbr[512] __aligned(16);
static struct sup_context context;
static struct mmc *card;
static struct blk_desc *disk;
static u32 bound_cid[4], bound_sectors;
static int bound, armed;
static ulong io_start, io_activity, sd_start;

#define UPDATE_GUARD ((volatile u32 *)0x43ff0)

int sup_io_live(void)
{
	return get_timer(io_start) < 120000 && get_timer(io_activity) < 10000;
}

void sup_io_progress(void)
{
	io_activity = get_timer(0);
}

int sup_sd_live(void)
{
	return get_timer(sd_start) < 10000;
}

u32 sup_crc32(const u8 *data, u32 bytes)
{
	return crc32(0, data, bytes);
}

static int guards_ok(void)
{
	u32 i;

	for (i = 0; i < 4; i++)
		if (UPDATE_GUARD[i] != 0x55504433U)
			return 0;
	return 1;
}

static u32 le32(const u8 *p)
{
	return p[0] | (u32)p[1] << 8 | (u32)p[2] << 16 | (u32)p[3] << 24;
}

static int read_blocks(u32 lba, u32 sectors, void *out)
{
	if (!disk || !sectors || lba >= disk->lba || sectors > disk->lba - lba || !guards_ok())
		return -1;
	sd_start = get_timer(0);
	return blk_dread(disk, lba, sectors, out) == sectors && sup_sd_live() && guards_ok() ? 0 : -1;
}

static int write_blocks(u32 slot, u32 offset, u32 sectors, const void *data)
{
	u32 lba;

	/* 固定入口、槽 0／1、原系統與當前更新器來源均不可寫。 */
	if (slot < 2 || slot >= SUP_SLOT_COUNT || slot == context.source ||
	    sup_slot(slot, &lba) || !sectors || offset >= 256 || sectors > 256 - offset ||
	    lba < 6656 || lba + offset + sectors > 7424 || !guards_ok())
		return -1;
	sd_start = get_timer(0);
	return blk_dwrite(disk, lba + offset, sectors, data) == sectors &&
	       sup_sd_live() && guards_ok() ? 0 : -1;
}

static int media(void)
{
	u32 start, sectors;

	sd_start = get_timer(0);
	if (!card) {
		if (mmc_initialize(NULL))
			return -1;
		card = find_mmc_device(0);
	}
	if (!card)
		return -1;
	card->has_init = 0;
	card->init_in_progress = 0;
	if (mmc_init(card) || !IS_SD(card) || !sup_sd_live())
		return -1;
	disk = mmc_get_blk_desc(card);
	if (disk->blksz != 512 || disk->lba < 131072 || disk->lba > 0xffffffffULL ||
	    read_blocks(0, 1, check_buffer))
		return -1;
	start = le32(check_buffer + 454);
	sectors = le32(check_buffer + 458);
	if (check_buffer[510] != 0x55 || check_buffer[511] != 0xaa ||
	    check_buffer[450] != 0x83 || start != 8192 ||
	    sectors < 4096 || sectors > disk->lba - start ||
	    sup_padding(check_buffer + 462, 48))
		return -1;
	if (bound) {
		if (memcmp(card->cid, bound_cid, sizeof(bound_cid)) ||
		    disk->lba != bound_sectors || memcmp(check_buffer, original_mbr, 512))
			return -1;
	} else {
		memcpy(bound_cid, card->cid, sizeof(bound_cid));
		bound_sectors = disk->lba;
		memcpy(original_mbr, check_buffer, 512);
		bound = 1;
	}
	return 0;
}

static void print_digest(const u8 *hash)
{
	u32 i;

	for (i = 0; i < 32; i++)
		printf("%02x", hash[i]);
}

static int hex_digest(const char *text, u8 *hash)
{
	u32 i, value, digit;

	if (!text || strlen(text) != 64)
		return -1;
	for (i = 0; i < 64; i++) {
		if (text[i] >= '0' && text[i] <= '9')
			digit = text[i] - '0';
		else if (text[i] >= 'a' && text[i] <= 'f')
			digit = text[i] - 'a' + 10;
		else
			return -1;
		value = i & 1 ? (u32)hash[i / 2] * 16 + digit : digit;
		hash[i / 2] = value;
	}
	return 0;
}

static int slot_digest(u32 slot, const u8 *pending_header, u8 *hash, u32 *bytes)
{
	struct sup_image image;
	sha256_context all, payload;
	u8 local_header[512] __aligned(16), payload_hash[32];
	u32 lba, position, count, data_count;

	if (sup_slot(slot, &lba))
		return -1;
	if (pending_header)
		memcpy(local_header, pending_header, 512);
	else if (read_blocks(lba, 1, local_header))
		return -1;
	if (sup_header(local_header, &image))
		return -1;
	sha256_starts(&all);
	sha256_starts(&payload);
	sha256_update(&all, local_header, 512);
	for (position = 0; position < image.package_bytes - 512; position += count) {
		count = min((u32)sizeof(check_buffer), image.package_bytes - 512 - position);
		if (read_blocks(lba + 1 + position / 512, count / 512, check_buffer))
			return -1;
		sha256_update(&all, check_buffer, count);
		data_count = position < image.image_bytes ? min(count, image.image_bytes - position) : 0;
		sha256_update(&payload, check_buffer, data_count);
		if (sup_padding(check_buffer + data_count, count - data_count))
			return -1;
	}
	sha256_finish(&payload, payload_hash);
	sha256_finish(&all, hash);
	if (memcmp(payload_hash, image.digest, 32))
		return -1;
	*bytes = image.package_bytes;
	return 0;
}

static int modem_getc(void)
{
	return sup_io_live() && tstc() ? getchar() : -1;
}

static int update(u32 slot, u32 expected, const u8 *expected_hash)
{
	connection_info_t info = {.mode = xyzModem_xmodem};
	struct sup_image image;
	u8 hash[32];
	u32 received = 0, body_offset, skip, verified_bytes;
	int count, error = 0, result = -1;

	if (slot < 2 || slot >= SUP_SLOT_COUNT || slot == context.source ||
	    expected < 1024 || expected > SUP_PACKAGE_MAX || (expected & 511))
		return -1;
	io_start = get_timer(0);
	io_activity = io_start;
	if (xyzModem_stream_open(&info, &error))
		return -1;
	for (;;) {
		count = xyzModem_stream_read((char *)buffer, sizeof(buffer), &error);
		if (count <= 0)
			break;
		if ((count & 511) || (u32)count > expected - received || !guards_ok())
			goto out;
		skip = 0;
		if (!received) {
			memcpy(header, buffer, 512);
			if (sup_header(header, &image) || image.package_bytes != expected ||
			    (slot == 2 ? image.kind != 3 : image.kind != 4))
				goto out;
			/* 先使目標槽無效；完整資料回讀通過後才寫入有效標頭。 */
			if (write_blocks(slot, 0, 1, zero_sector))
				goto out;
			skip = 512;
		}
		body_offset = received + skip;
		if ((u32)count > skip &&
		    write_blocks(slot, body_offset / 512, (count - skip) / 512, buffer + skip))
			goto out;
		received += count;
	}
	if (received != expected || error || !sup_io_live())
		goto out;
	if (slot_digest(slot, header, hash, &verified_bytes) || verified_bytes != expected ||
	    memcmp(hash, expected_hash, 32) || write_blocks(slot, 0, 1, header))
		goto out;
	if (slot_digest(slot, NULL, hash, &verified_bytes) || verified_bytes != expected ||
	    memcmp(hash, expected_hash, 32))
		goto out;
	result = 0;
out:
	xyzModem_stream_close(&error);
	xyzModem_stream_terminate(result != 0, modem_getc);
	return result;
}

static int command(char *line, u32 capacity)
{
	u32 bytes = 0;
	ulong start = get_timer(0);
	int c, invalid = 0;

	for (;;) {
		if ((bytes || invalid) && get_timer(start) > 5000)
			return -1;
		if (!tstc())
			continue;
		c = getchar();
		if (c == '\n' || c == '\r') {
			if (!bytes && !invalid)
				continue;
			line[bytes] = 0;
			return invalid ? -1 : 0;
		}
		if (!bytes && !invalid)
			start = get_timer(0);
		if (c < 32 || c > 126 || bytes + 1 >= capacity)
			invalid = 1;
		else if (!invalid)
			line[bytes++] = c;
	}
}

void bpi_update_run(ulong incoming)
{
	char line[160], *fields[5], *cursor;
	u8 hash[32];
	u32 count, nonce, slot, bytes, i;
	int result;

	if (incoming < 0x48010 || incoming > 0x4fff0 - sizeof(context))
		goto halt;
	memcpy(&context, (const void *)incoming, sizeof(context));
	if (context.magic != SUP_CONTEXT_MAGIC || context.abi != 3 ||
	    context.bytes != sizeof(context) || context.board != SUP_BOARD_ID ||
	    context.runtime_bytes != SUP_RUNTIME_MAX ||
	    (context.source != 0xffffffffU && context.source >= SUP_SLOT_COUNT))
		goto halt;
	for (i = 0; i < 4; i++)
		UPDATE_GUARD[i] = 0x55504433U;
	printf("BPI-SPL2 event=update-ready nonce_hex=%08x abi=3 kind=3 ddr=off write_slots=2,3,4\n", context.nonce);
	for (;;) {
		if (!guards_ok())
			goto halt;
		if (command(line, sizeof(line)))
			goto reject;
		cursor = line;
		for (count = 0; count < 5; count++) {
			fields[count] = strsep(&cursor, " ");
			if (!fields[count])
				break;
		}
		if (cursor || count < 2 || strlen(fields[0]) != 1 ||
		    sup_decimal(fields[1], 0xffffffffU, &nonce) || nonce != context.nonce)
			goto reject;
		if (fields[0][0] == 'I' && count == 2) {
			armed = 0;
			if (media())
				goto reject;
			armed = 1;
			printf("BPI-SPL2 event=media nonce_hex=%08x cid=", nonce);
			for (i = 0; i < 4; i++)
				printf("%08x", bound_cid[i]);
			printf(" sectors=%u partition_start=8192 partition_sectors=%u\n",
			       bound_sectors, le32(original_mbr + 458));
			continue;
		}
		if (!armed || count < 3 || sup_decimal(fields[2], 4, &slot))
			goto reject;
		if (fields[0][0] == 'H' && count == 3) {
			result = slot_digest(slot, NULL, hash, &bytes);
			printf("BPI-SPL2 event=slot nonce_hex=%08x slot=%u result=%d", nonce, slot, result);
			if (!result) {
				printf(" bytes=%u sha256=", bytes);
				print_digest(hash);
			}
			puts("\n");
			continue;
		}
		if (fields[0][0] != 'W' || count != 5 || slot < 2 || slot == context.source ||
		    sup_decimal(fields[3], SUP_PACKAGE_MAX, &bytes) || bytes < 1024 || (bytes & 511) ||
		    hex_digest(fields[4], hash))
			goto reject;
		armed = 0;
		if (media())
			goto reject;
		printf("BPI-SPL2 event=update-loading nonce_hex=%08x slot=%u bytes=%u\n", nonce, slot, bytes);
		result = update(slot, bytes, hash);
		printf("BPI-SPL2 event=update-result nonce_hex=%08x slot=%u result=%d committed=%u sha256=",
		       nonce, slot, result, result == 0);
		if (!result)
			print_digest(hash);
		else
			puts("none");
		puts("\n");
		continue;
reject:
		armed = 0;
		printf("BPI-SPL2 event=update-reject nonce_hex=%08x reason=command_or_media\n", context.nonce);
	}
halt:
	puts("BPI-SPL2 event=update-halt reason=context_or_guard\n");
	for (;;)
		asm volatile("wfe");
}
