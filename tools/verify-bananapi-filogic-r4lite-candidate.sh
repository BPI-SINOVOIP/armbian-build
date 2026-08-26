#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
verifier="${repo_dir}/tools/verify-bananapi-filogic-candidates.sh"

VALIDATION_CONFIG="${repo_dir}/config/validation/bananapi-filogic-mt7987-r4lite-current.json" \
	OUTPUT_DIR="${repo_dir}/output/images/2026.08/bananapi-filogic-mt7987-r4lite-trixie-current-cli" \
	BOARDS="bananapir4lite" VERIFY_TMP_PREFIX="filogic-r4lite-verify" \
	"${verifier}" "$@"
