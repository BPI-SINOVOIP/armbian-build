#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export CANDIDATE_BUILDER="${CANDIDATE_BUILDER:-${repo_dir}/tools/build-bananapi-meson-candidates.sh}"
exec "${repo_dir}/tools/run-bananapi-candidates-isolated-cache.sh" "$@"
