#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
policy="${VALIDATION_CONFIG:-${repo_dir}/config/validation/bananapi-filogic-mt7986-r3mini-current.json}"

exec python3 "${repo_dir}/tools/check-bananapi-filogic-r3mini-policy.py" "${policy}" "$@"
