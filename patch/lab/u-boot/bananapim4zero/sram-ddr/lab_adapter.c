/* SPDX-License-Identifier: GPL-2.0+ */
#include "payload.h"

/* 保留原演算法與解析器；舊啟動迴圈不作為新負載入口。 */
#include "dram_sun50i_h616_lab.c"

int ddr_parse(char *line, u32 nonce, struct ddr_request *request)
{
	const char prefix[] = "R nonce_hex=";
	struct lab_request parsed;
	u32 actual = 0, digit;
	unsigned int i;

	for (i = 0; i < sizeof(prefix) - 1; i++)
		if (line[i] != prefix[i])
			return DDR_PARSE_NONCE;
	for (; i < sizeof(prefix) - 1 + 8; i++) {
		if (!lab_hex(line[i], &digit))
			return DDR_PARSE_NONCE;
		actual = actual * 16 + digit;
	}
	if (line[i] != ' ' || actual != nonce)
		return DDR_PARSE_NONCE;
	line[i - 1] = 'R';
	if (!lab_parse(line + i - 1, &parsed))
		return DDR_PARSE_FIELDS;
	if (parsed.passes != 1)
		return DDR_PARSE_PASSES;
	*request = (struct ddr_request) {
		.id = parsed.id, .para = parsed.para, .level = parsed.level,
		.passes = parsed.passes, .window_mib = parsed.window_mib,
	};
	return DDR_PARSE_OK;
}

bool ddr_geometry_valid(const struct dram_config *config, u32 window_mib, ulong *size)
{
	ulong bytes = (ulong)window_mib << 20;

	if (config->cols < 8 || config->cols > 11 ||
	    config->rows < 13 || config->rows > 17 ||
	    (config->ranks != 1 && config->ranks != 2) ||
	    config->bus_full_width > 1 || !window_mib || window_mib > 64)
		return false;
	*size = mctl_calc_size(config);
	return *size >= (256UL << 20) && *size <= (4UL << 30) &&
	       bytes * 4 < *size && (136UL << 20) + bytes <= *size;
}

bool ddr_lab_test(const struct ddr_request *request, const struct dram_config *config)
{
	struct lab_request legacy = {
		.id = request->id, .para = request->para, .level = request->level,
		.passes = request->passes, .window_mib = request->window_mib,
	};
	ulong size;

	if (!ddr_geometry_valid(config, request->window_mib, &size))
		return false;
	lab_config = *config;
	lab_size = size;
	return lab_test(&legacy);
}
