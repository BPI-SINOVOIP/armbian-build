/* SPDX-License-Identifier: GPL-2.0+ */
#include "payload.h"

bool ddr_context_valid(const struct ddr_context *ctx, ulong address,
		       ulong el, ulong sp, ulong sctlr, u32 image_bytes)
{
	if (address < 0x48010 || address > 0x4fff0 - sizeof(*ctx) ||
	    (address & 7) || el != 12 || sp <= 0x40010 || sp >= 0x47800 ||
	    (sp & 15) || (sctlr & 0x1005))
		return false;
	return ctx->magic == DDR_CONTEXT_MAGIC && ctx->abi == DDR_ABI &&
	       ctx->bytes == sizeof(*ctx) && ctx->board == DDR_BOARD &&
	       (ctx->source == 0xffffffffU || ctx->source < 5) &&
	       ctx->image_bytes == image_bytes && image_bytes > 0 &&
	       image_bytes <= DDR_RUNTIME_BYTES - 512 &&
	       ctx->runtime_bytes == DDR_RUNTIME_BYTES;
}

int ddr_line_feed(struct ddr_line *line, unsigned char c)
{
	if (c == '\r' || c == '\n') {
		if (line->invalid) {
			line->invalid = false;
			line->used = 0;
			return -1;
		}
		if (!line->used)
			return 0;
		line->text[line->used] = 0;
		line->used = 0;
		return 1;
	}
	if (c < 0x20 || c > 0x7e || line->used >= DDR_LINE_BYTES - 1)
		line->invalid = true;
	else if (!line->invalid)
		line->text[line->used++] = c;
	return 0;
}
