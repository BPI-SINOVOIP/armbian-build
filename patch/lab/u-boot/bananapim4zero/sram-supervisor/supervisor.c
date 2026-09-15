/* SPDX-License-Identifier: GPL-2.0+ */
#include <common.h>
#include <mmc.h>
#include <serial.h>
#include <xyzModem.h>
#include <u-boot/crc.h>
#include <u-boot/sha256.h>
#include <asm/system.h>
#include "supervisor.h"

static u8 header[SUP_HEADER_BYTES] __aligned(16);
static u8 transfer[1024] __aligned(16);
static struct sup_image loaded;
static struct sup_context context;
static int verified;
static struct mmc *card;
static ulong transfer_start;
static ulong transfer_activity;
static ulong sd_start;
static const unsigned long guard_addresses[] = {0x48000, 0x4fff0, 0x50000};

static void init_guards(void)
{
	u32 i, j;

	for (i = 0; i < ARRAY_SIZE(guard_addresses); ++i)
		for (j = 0; j < 4; ++j)
			((volatile u32 *)guard_addresses[i])[j] = 0x5352414dU;
}

static int guards_valid(void)
{
	u32 i, j;

	for (i = 0; i < ARRAY_SIZE(guard_addresses); ++i)
		for (j = 0; j < 4; ++j)
			if (((volatile u32 *)guard_addresses[i])[j] != 0x5352414dU)
				return 0;
	return 1;
}

int sup_io_live(void)
{
	return get_timer(transfer_start) < 120000 &&
	       get_timer(transfer_activity) < 10000;
}

void sup_io_progress(void)
{
	transfer_activity = get_timer(0);
}

int sup_sd_live(void)
{
	return get_timer(sd_start) < 10000;
}

u32 sup_crc32(const u8 *data, u32 bytes)
{
	return crc32(0, data, bytes);
}

static int digest_matches(void)
{
	u8 digest[32];

	sha256_csum_wd((const u8 *)SUP_LOAD_BASE, loaded.image_bytes,
		       digest, 4096);
	return !memcmp(digest, loaded.digest, sizeof(digest));
}

static int finalise(void)
{
	u32 padded = loaded.package_bytes - SUP_HEADER_BYTES;

	if (!guards_valid() || sup_padding((const u8 *)SUP_LOAD_BASE + loaded.image_bytes,
			padded - loaded.image_bytes) || !digest_matches())
		return -1;
	verified = 1;
	return 0;
}

static int modem_getc(void)
{
	return sup_io_live() && tstc() ? getchar() : -1;
}

static int receive(u32 expected)
{
	connection_info_t info = {.mode = xyzModem_xmodem};
	u32 received = 0, skip, copy;
	int error = 0, count, result = -1;

	verified = 0;
	transfer_start = get_timer(0);
	transfer_activity = transfer_start;
	if (expected < 1024 || expected > SUP_PACKAGE_MAX || (expected & 511))
		return -1;
	if (xyzModem_stream_open(&info, &error))
		return -1;
	for (;;) {
		count = xyzModem_stream_read((char *)transfer, sizeof(transfer), &error);
		if (count <= 0)
			break;
		if ((u32)count > expected - received)
			goto out;
		skip = 0;
		if (received < SUP_HEADER_BYTES) {
			copy = min((u32)count, SUP_HEADER_BYTES - received);
			memcpy(header + received, transfer, copy);
			skip = copy;
			if (received + copy == SUP_HEADER_BYTES &&
			    (sup_header(header, &loaded) || loaded.package_bytes != expected))
				goto out;
		}
		if ((u32)count > skip)
			memcpy((u8 *)SUP_LOAD_BASE + received + skip - SUP_HEADER_BYTES,
			       transfer + skip, count - skip);
		received += count;
	}
	if (received == expected && error == 0 && sup_io_live())
		result = finalise();
out:
	xyzModem_stream_close(&error);
	xyzModem_stream_terminate(result != 0, modem_getc);
	if (result)
		verified = 0;
	return result;
}

static int load_slot(u32 index)
{
	u32 lba, sectors;
	struct blk_desc *desc;

	verified = 0;
	sd_start = get_timer(0);
	if (sup_slot(index, &lba))
		return -1;
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
	desc = mmc_get_blk_desc(card);
	if (desc->blksz != 512 || desc->lba < 131072 ||
	    blk_dread(desc, lba, 1, header) != 1 || sup_header(header, &loaded))
		return -1;
	sectors = loaded.package_bytes / 512 - 1;
	if (sectors > SUP_SLOT_BYTES / 512 - 1 ||
	    blk_dread(desc, lba + 1, sectors, (void *)SUP_LOAD_BASE) != sectors ||
	    !sup_sd_live())
		return -1;
	return finalise();
}

static int read_command(char *line, u32 capacity)
{
	u32 bytes = 0;
	int c, invalid = 0;
	ulong start = get_timer(0);

	for (;;) {
		if ((bytes || invalid) && get_timer(start) > 5000)
			return -1;
		if (!tstc()) {
			if ((bytes || invalid) && get_timer(start) > 5000)
				return -1;
			continue;
		}
		c = getchar();
		if (c == '\r' || c == '\n') {
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

void bpi_supervisor_run(void)
{
	char line[96], *nonce_text, *argument;
	u32 nonce, value, source = 0, loaded_nonce = 0;
	int result;

	init_guards();
#ifdef CONFIG_BPI_SRAM_DDR_V2
	puts("BPI-SUP1 event=ready abi=2 board=06180001 ddr=off sd_write=off\n");
#else
	puts("BPI-SUP1 event=ready abi=1 board=06180001 ddr=off sd_write=off\n");
#endif
	for (;;) {
		if (!guards_valid()) {
			puts("BPI-SUP1 event=halt reason=guard\n");
			for (;;)
				asm volatile("wfe");
		}
		if (read_command(line, sizeof(line))) {
			verified = 0;
			puts("BPI-SUP1 event=reject reason=syntax\n");
			continue;
		}
		if (line[1] != ' ')
			goto reject;
		nonce_text = line + 2;
		argument = strchr(nonce_text, ' ');
		if (argument)
			*argument++ = 0;
		if (sup_decimal(nonce_text, 0xffffffffU, &nonce))
			goto reject;
		if (line[0] == 'I' && !argument) {
#ifdef CONFIG_BPI_SRAM_DDR_V2
			printf("BPI-SUP1 event=info nonce=%u abi=2 board=06180001 capabilities=uart-ram,sd-read,smoke-run,ddr-run\n", nonce);
#else
			printf("BPI-SUP1 event=info nonce=%u abi=1 board=06180001 capabilities=uart-ram,sd-read,smoke-run\n", nonce);
#endif
			continue;
		}
		if ((line[0] == 'U' || line[0] == 'S') && argument &&
		    !sup_decimal(argument, SUP_PACKAGE_MAX, &value)) {
			printf("BPI-SUP1 event=loading nonce=%u transport=%c\n", nonce, line[0]);
			result = line[0] == 'U' ? receive(value) : load_slot(value);
			source = line[0] == 'U' ? 0xffffffffU : value;
			loaded_nonce = nonce;
#ifdef CONFIG_BPI_SRAM_DDR_V2
			printf("BPI-SUP1 event=loaded nonce=%u result=%d kind=%u sha256=",
			       nonce, result, result ? 0 : loaded.kind);
			if (result) {
				puts("none\n");
			} else {
				for (u32 i = 0; i < sizeof(loaded.digest); i++)
					printf("%02x", loaded.digest[i]);
				puts("\n");
			}
#else
			printf("BPI-SUP1 event=loaded nonce=%u result=%d\n", nonce, result);
#endif
			continue;
		}
		if (line[0] == 'R' && !argument && verified && nonce == loaded_nonce &&
		    current_el() == 3 && !(get_sctlr() & (CR_M | CR_C | CR_I)) &&
		    guards_valid() && digest_matches()) {
			context.magic = SUP_CONTEXT_MAGIC;
			context.abi = loaded.kind;
			context.bytes = sizeof(context);
			context.board = SUP_BOARD_ID;
			context.nonce = nonce;
			context.source = source;
			context.image_bytes = loaded.image_bytes;
			context.runtime_bytes = loaded.runtime_bytes;
			memcpy(context.digest, loaded.digest, sizeof(context.digest));
			verified = 0;
			printf("BPI-SUP1 event=handoff nonce=%u entry=00030000\n", nonce);
			sup_enter(SUP_LOAD_BASE, &context);
		}
reject:
		verified = 0;
		puts("BPI-SUP1 event=reject reason=command_or_state\n");
	}
}
