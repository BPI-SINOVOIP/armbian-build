#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
runner="${repo_dir}/tools/run-bananapi-filogic-candidates-isolated-cache.sh"

export VALIDATION_CONFIG="${repo_dir}/config/validation/bananapi-filogic-mt7622-r64-current.json"
export OUTPUT_DIR="${repo_dir}/output/images/2026.08/bananapi-filogic-mt7622-r64-trixie-current-cli"
export BOARDS="bananapir64"
export CACHE_OVERLAY_ROOT="${repo_dir}/.tmp/bananapi-filogic-r64-cache-overlay"

"${runner}" "$@"
