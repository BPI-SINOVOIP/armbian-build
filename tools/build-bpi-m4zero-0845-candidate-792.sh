#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

EXPERIMENT=A1 \
	EXPECT_DIAGNOSTICS=yes \
	EXPECT_LAB=no \
	PROFILE_DESCRIPTION="0845 實測收斂 792 MHz 工程候選" \
	EXPECTED_CLK=792 \
	EXPECTED_DX_ODT=0x07070707 \
	EXPECTED_DX_DRI=0x0e0e0e0e \
	EXPECTED_CA_DRI=0x0d0d \
	EXPECTED_ODT_EN=0xaaaaeeee \
	EXPECTED_TPR6=0x3a808080 \
	EXPECTED_TPR10=0x402f6663 \
	EXPECTED_TPR11=0x25252523 \
	EXPECTED_TPR12=0x110f0f10 \
	exec "$script_dir/build-bpi-m4zero-opi-ddr-o0.sh" "$@"
