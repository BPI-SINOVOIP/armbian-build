/* SPDX-License-Identifier: GPL-2.0+ */
#ifndef BPI_SRAM_SUPERVISOR_H
#define BPI_SRAM_SUPERVISOR_H

#ifdef SUP_HOST_TEST
#include <stddef.h>
#include <stdint.h>
typedef uint8_t u8;
typedef uint32_t u32;
#else
#include <linux/types.h>
#endif

#define SUP_BOARD_ID 0x06180001U
#define SUP_HEADER_BYTES 512U
#define SUP_LOAD_BASE 0x30000U
#define SUP_RUNTIME_MAX 0x18000U
#define SUP_IMAGE_MAX (SUP_RUNTIME_MAX - 512U)
#define SUP_PACKAGE_MAX (SUP_HEADER_BYTES + SUP_RUNTIME_MAX)
#define SUP_SLOT_BYTES 0x20000U
#define SUP_SLOT_COUNT 5U
#define SUP_FIRST_SLOT_LBA 6144U
#define SUP_SLOT_STRIDE_LBA 2048U
#define SUP_CONTEXT_MAGIC 0x31505553U

struct sup_image {
	u32 image_bytes;
	u32 runtime_bytes;
	u32 package_bytes;
	u32 kind;
	u8 digest[32];
};

struct sup_context {
	u32 magic;
	u32 abi;
	u32 bytes;
	u32 board;
	u32 nonce;
	u32 source;
	u32 image_bytes;
	u32 runtime_bytes;
	u8 digest[32];
};

u32 sup_crc32(const u8 *data, u32 bytes);
int sup_header(const u8 *header, struct sup_image *out);
int sup_padding(const u8 *data, u32 bytes);
int sup_decimal(const char *text, u32 limit, u32 *out);
int sup_slot(u32 index, u32 *lba);
#ifndef SUP_HOST_TEST
void sup_prepare(void);
int sup_io_live(void);
int sup_sd_live(void);
void bpi_supervisor_run(void) __attribute__((noreturn));
void sup_enter(unsigned long entry, const struct sup_context *context)
	__attribute__((noreturn));
#endif

#endif
