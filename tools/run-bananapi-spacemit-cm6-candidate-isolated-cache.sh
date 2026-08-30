#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
runner="${repo_dir}/tools/run-bananapi-candidates-isolated-cache.sh"

export CANDIDATE_BUILDER="${repo_dir}/tools/build-bananapi-spacemit-candidates.sh"
export VALIDATION_CONFIG="${repo_dir}/config/validation/bananapi-spacemit-k1-cm6-legacy.json"
export OUTPUT_DIR="${repo_dir}/output/images/2026.08/bananapi-spacemit-k1-cm6-trixie-legacy-cli"
export BOARDS="bananapicm6"
export CACHE_OVERLAY_ROOT="${repo_dir}/.tmp/bananapi-spacemit-cm6-cache-overlay"

"${runner}" "$@"
