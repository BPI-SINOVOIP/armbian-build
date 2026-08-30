#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
verifier="${repo_dir}/tools/verify-bananapi-filogic-candidates.sh"

VALIDATION_CONFIG="${repo_dir}/config/validation/bananapi-filogic-mt7622-r64-current.json" \
	OUTPUT_DIR="${repo_dir}/output/images/2026.08/bananapi-filogic-mt7622-r64-trixie-current-cli" \
	BOARDS="bananapir64" VERIFY_TMP_PREFIX="filogic-r64-verify" \
	"${verifier}" "$@"
