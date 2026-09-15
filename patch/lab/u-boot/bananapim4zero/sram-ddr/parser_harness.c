/* SPDX-License-Identifier: GPL-2.0+ */
/* 僅在 QEMU 使用者模式測試純解析、框架與數值驗證，不執行 MMIO。 */
#include "payload.h"
extern unsigned long strtoul(const char *text, char **end, int base);
extern long write(int fd, const void *data, unsigned long bytes);

void putc(char c)
{
	(void)write(1, &c, 1);
}

int main(int argc, char **argv)
{
	struct ddr_request request = {0};
	struct ddr_context ctx = {
		.magic = DDR_CONTEXT_MAGIC, .abi = DDR_ABI, .bytes = 64,
		.board = DDR_BOARD, .nonce = 0x12345678, .source = 0xffffffffU,
		.image_bytes = 16384, .runtime_bytes = DDR_RUNTIME_BYTES,
	};
	int status;

	if (argc == 2 && !strcmp(argv[1], "format")) {
		printf("%016lx %lu %d\n", ~0UL, ~0UL, (-2147483647 - 1));
		return 0;
	}
	if (argc == 3 && !strcmp(argv[1], "parse")) {
		status = ddr_parse(argv[2], ctx.nonce, &request);
		printf("%d %u %u %u %u %u\n", status, request.id,
		       request.para.clk, request.para.type, request.passes, request.window_mib);
		return 0;
	}
	if (argc == 7 && !strcmp(argv[1], "geometry")) {
		struct dram_config geometry = {
			.cols = strtoul(argv[2], NULL, 0), .rows = strtoul(argv[3], NULL, 0),
			.ranks = strtoul(argv[4], NULL, 0), .bus_full_width = strtoul(argv[5], NULL, 0),
		};
		ulong size = 0;
		bool valid = ddr_geometry_valid(&geometry, strtoul(argv[6], NULL, 0), &size);
		printf("%u %lu %u\n", valid, size, ddr_preflight(&geometry));
		return 0;
	}
	if ((argc == 9 || argc == 10) && !strcmp(argv[1], "context")) {
		ctx.abi = strtoul(argv[2], NULL, 0);
		ctx.bytes = strtoul(argv[3], NULL, 0);
		ctx.runtime_bytes = strtoul(argv[4], NULL, 0);
		if (argc == 10)
			ctx.nonce = strtoul(argv[9], NULL, 0);
		printf("%u\n", ddr_context_valid(&ctx, strtoul(argv[5], NULL, 0),
		       strtoul(argv[6], NULL, 0), strtoul(argv[7], NULL, 0),
		       strtoul(argv[8], NULL, 0), 16384));
		return 0;
	}
	if (argc == 3 && !strcmp(argv[1], "frame")) {
		struct ddr_line line = {0};
		u32 ready = 0, rejected = 0;
		char byte[3] = {0};
		const char *input = argv[2];
		while (input[0] && input[1]) {
			byte[0] = *input++;
			byte[1] = *input++;
			status = ddr_line_feed(&line, strtoul(byte, NULL, 16));
			ready += status == 1;
			rejected += status == -1;
		}
		printf("%u %u %u %u\n", ready, rejected, line.used, line.invalid);
		return 0;
	}
	return 2;
}
