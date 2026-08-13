#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

EXPERIMENT=X2 \
	EXPECT_DIAGNOSTICS=yes \
	EXPECT_LAB=no \
	PROFILE_DESCRIPTION="0438 與 1116 跨板 792 MHz 候選" \
	EXPECTED_CLK=792 \
	EXPECTED_DX_ODT=0x07070707 \
	EXPECTED_DX_DRI=0x0e0e0e0e \
	EXPECTED_CA_DRI=0x0d0d \
	EXPECTED_ODT_EN=0xaaaaeeee \
	EXPECTED_TPR6=0x3a808080 \
	EXPECTED_TPR10=0x402f6663 \
	EXPECTED_TPR11=0x24242422 \
	EXPECTED_TPR12=0x110f1111 \
	exec "$script_dir/build-bpi-m4zero-opi-ddr-o0.sh" "$@"
