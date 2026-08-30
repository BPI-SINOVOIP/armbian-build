#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
runner="${repo_dir}/tools/run-bananapi-filogic-candidates-isolated-cache.sh"

export VALIDATION_CONFIG="${repo_dir}/config/validation/bananapi-filogic-mt7987-r4lite-current.json"
export OUTPUT_DIR="${repo_dir}/output/images/2026.08/bananapi-filogic-mt7987-r4lite-trixie-current-cli"
export BOARDS="bananapir4lite"
export CACHE_OVERLAY_ROOT="${repo_dir}/.tmp/bananapi-filogic-r4lite-cache-overlay"

"${runner}" "$@"
