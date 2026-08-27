#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
builder="${repo_dir}/tools/build-bananapi-sunxi-candidates.sh"

export VALIDATION_CONFIG="${repo_dir}/config/validation/bananapi-mt7623-r2-current.json"
export OUTPUT_DIR="${repo_dir}/output/images/2026.08/bananapi-mt7623-r2-trixie-current-cli"
export BOARDS="bananapir2"
export CANDIDATE_FAMILY_NAME="MT7623"
export CANDIDATE_LOCK_FILE=".bananapi-mt7623-build.lock"
export MINIMUM_FREE_GIB="${MINIMUM_FREE_GIB:-40}"

"${builder}" "$@"
