#!/usr/bin/env bash
# 使用已解包的官方 Python；隔離網路與硬體，檔案系統唯讀。
set -euo pipefail
if [[ $# -lt 3 || $# -gt 4 ]]; then
    echo '用法：run_matcher.sh 官方解包目錄 嵌入執行器 分割表目錄 [實包目錄]' >&2
    exit 2
fi
titan_bundle=$(realpath "$1")
titan_runner=$(realpath "$2")
titan_fixture=$(realpath "$3")
titan_scripts=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
titan_extra=()
if [[ $# == 4 ]]; then
    titan_extra=(--setenv TITAN_PACKAGE_DIR "$(realpath "$4")")
fi
exec bwrap --unshare-all --ro-bind / / --dev /dev --proc /proc \
    --setenv TITAN_EXTRACT_ROOT "$titan_bundle" \
    --setenv TITAN_DIAGNOSTIC_SCRIPT "$titan_scripts/reproduce_matcher.py" \
    --setenv TITAN_FIXTURE_DIR "$titan_fixture" \
    --setenv LD_LIBRARY_PATH "$titan_bundle" \
    "${titan_extra[@]}" \
    "$titan_runner" "$titan_bundle/libpython3.8.so.1.0" \
    "$titan_bundle/base_library.zip:$titan_bundle:$titan_bundle/lib-dynload:$titan_bundle/python3.8/lib-dynload" \
    "$titan_scripts/bundle_bootstrap.py"
