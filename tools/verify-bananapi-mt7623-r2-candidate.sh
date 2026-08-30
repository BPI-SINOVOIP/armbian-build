#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
verifier="${repo_dir}/tools/verify-bananapi-sunxi-candidates.sh"

export VALIDATION_CONFIG="${repo_dir}/config/validation/bananapi-mt7623-r2-current.json"
export OUTPUT_DIR="${repo_dir}/output/images/2026.08/bananapi-mt7623-r2-trixie-current-cli"
export BOARDS="bananapir2"
export CANDIDATE_FAMILY_NAME="MT7623"
export VERIFY_TMP_PREFIX="bananapi-mt7623-r2-verify"

"${verifier}" "$@"
