#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_dir="$(cd "$script_dir/.." && pwd)"
evidence_dir="${EVIDENCE_DIR:-$repo_dir/output/evidence/bpi-m4zero-opi-ddr/A1-20260819-0845-candidate-6e05b3313}"
deb_name="linux-u-boot-bananapim4zero-current_26.05.0-trunk_arm64__2026.01-S127a-P02e5-Hc6a9-V3946-Be6d8-R448a.deb"

MATRIX_LABEL="A1 0845 實測收斂 792 MHz" \
	ARTIFACT_TAG="a1-0845-validated-lanes-792mhz" \
	OUTPUT_DIR="${OUTPUT_DIR:-$repo_dir/output/images/2026.08/bpi-m4zero-a1-0845-792-matrix}" \
	WORK_DIR="${WORK_DIR:-$repo_dir/.tmp/bpi-m4zero-a1-792-matrix-20260819}" \
	EVIDENCE_DIR="$evidence_dir" \
	UBOOT_DEB="${UBOOT_DEB:-$evidence_dir/$deb_name}" \
	DELIVERY_DOC="${DELIVERY_DOC:-$repo_dir/docs/bananapi-m4zero-a1-792-image-matrix-delivery-20260820.md}" \
	VALIDATE_DELIVERY_TABLE=yes \
	EXPECTED_DEB_SHA256=4add1a5a9aa6b32cf25e7a145e48226768e901d9af6d670c41e6ebe27ac4de48 \
	EXPECTED_UBOOT_SHA256=0b9333deac4a63353eb18442c9ef2f7ef269be1d7ef015cae3eee65f1b92a0cf \
	EXPECTED_BUILD_ID=P02e5 \
	METADATA_QUALIFICATION=a1_0845_m4zlab2_pass_cold_boot_pending \
	MATRIX_QUALIFICATION=A1_0845_M4ZLAB2_PASS_COLD_BOOT_PENDING \
	HARDWARE_G1_PASS=none \
	REQUIRED_NEXT_STEP=0845_cold_boot_and_linux_memory_stress \
	exec "$script_dir/build-bpi-m4zero-x2-792-matrix.sh" "$@"
