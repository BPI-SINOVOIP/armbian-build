#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_dir="$(cd "$script_dir/.." && pwd)"

PACKAGE_LABEL="X2 跨板 792 MHz 候選" \
	OUTPUT_DIR="${OUTPUT_DIR:-$repo_dir/output/images/2026.08/bpi-m4zero-cross-board-792}" \
	OUTPUT_IMAGE="${OUTPUT_IMAGE:-$repo_dir/output/images/2026.08/bpi-m4zero-cross-board-792/Armbian-unofficial_26.05.0-trunk_Bananapim4zero_jammy_current_6.18.32_x2-cross-board-792mhz.img}" \
	exec "$script_dir/package-bpi-m4zero-o1-test-image.sh" "$@"
