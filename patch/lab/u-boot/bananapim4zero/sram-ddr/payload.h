/* SPDX-License-Identifier: GPL-2.0+ */
#ifndef BPI_DDR_PAYLOAD_H
#define BPI_DDR_PAYLOAD_H
#include "compat.h"
#include <asm/arch/dram.h>
#include <asm/arch/dram_dw_helpers.h>

#define DDR_ENTRY 0x30000UL
#define DDR_END 0x48000UL
#define DDR_RUNTIME_BYTES 0x18000U
#define DDR_LINE_BYTES 384U
#define DDR_CONTEXT_MAGIC 0x31505553U
#define DDR_ABI 2U
#define DDR_KIND 2U
#define DDR_BOARD 0x06180001U

struct ddr_context {
	u32 magic, abi, bytes, board, nonce, source, image_bytes, runtime_bytes;
	u8 digest[32];
};
_Static_assert(sizeof(struct ddr_context) == 64, "交接上下文大小不符");

struct ddr_request {
	u32 id;
	struct dram_para para;
	u32 level, passes, window_mib;
};

struct ddr_line {
	char text[DDR_LINE_BYTES];
	u32 used;
	bool invalid;
};

enum ddr_parse_result { DDR_PARSE_OK, DDR_PARSE_NONCE, DDR_PARSE_FIELDS, DDR_PARSE_PASSES };
bool ddr_context_valid(const struct ddr_context *ctx, ulong address,
		       ulong el, ulong sp, ulong sctlr, u32 image_bytes);
int ddr_line_feed(struct ddr_line *line, unsigned char c);
int ddr_parse(char *line, u32 nonce, struct ddr_request *request);
bool ddr_geometry_valid(const struct dram_config *config, u32 window_mib, ulong *size);
bool ddr_lab_test(const struct ddr_request *request, const struct dram_config *config);
bool ddr_preflight(struct dram_config *config);
__noreturn void ddr_main(const struct ddr_context *context);
__noreturn void ddr_halt(void);
__noreturn void ddr_exception(ulong esr, ulong far);
#endif
