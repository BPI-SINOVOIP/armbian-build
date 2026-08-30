#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
runner="${repo_dir}/tools/run-bananapi-candidates-isolated-cache.sh"

export CANDIDATE_BUILDER="${repo_dir}/tools/build-bananapi-renesas-ai2n-candidate.sh"
export VALIDATION_CONFIG="${repo_dir}/config/validation/bananapi-renesas-rzv2n-ai2n-legacy.json"
export OUTPUT_DIR="${repo_dir}/output/images/2026.08/bananapi-renesas-rzv2n-ai2n-trixie-legacy-cli"
export CACHE_OVERLAY_ROOT="${repo_dir}/.tmp/bananapi-renesas-ai2n-cache-overlay"

"${runner}" "$@"
