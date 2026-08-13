#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

EXPERIMENT=O1 \
	EXPECT_DIAGNOSTICS=yes \
	exec "$script_dir/build-bpi-m4zero-opi-ddr-o0.sh" "$@"
