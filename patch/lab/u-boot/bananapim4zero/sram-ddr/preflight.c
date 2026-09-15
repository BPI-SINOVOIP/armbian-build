/* SPDX-License-Identifier: GPL-2.0+ */
#include "payload.h"

bool ddr_preflight(struct dram_config *config)
{
	/* 尚無經核准的 PMIC 狀態與當輪幾何證據，不允許參數繞過。 */
	(void)config;
	return false;
}
