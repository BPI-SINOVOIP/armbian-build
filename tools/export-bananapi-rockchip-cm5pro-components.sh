#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
config="${repo_dir}/config/validation/bananapi-rockchip-rk3576-cm5pro-vendor.json"
source_root="${CM5PRO_COMPONENT_SOURCE_ROOT:-${repo_dir}/.tmp/cm5pro-component}"
output_dir="${CM5PRO_COMPONENT_OUTPUT_DIR:-${repo_dir}/output/components/2026.08/bananapi-rockchip-rk3576-cm5pro-vendor}"

for command in git mktemp mv python3; do
	command -v "${command}" >/dev/null || {
		echo "CM5 Pro 元件匯出缺少必要命令：${command}" >&2
		exit 1
	}
done

fail() {
	echo "CM5 Pro 元件匯出失敗：$*" >&2
	exit 1
}

[[ -f "${config}" ]] || fail "找不到驗證契約"
[[ -d "${source_root}" ]] || fail "找不到元件來源根目錄：${source_root}"
[[ ! -e "${output_dir}" ]] || fail "輸出目錄已存在，拒絕覆寫：${output_dir}"

declare -A expected_commits=(
	[linux]="c6157104418d012823413c02f9222f3fe123dd25"
	[uboot]="39cd993e5d6296635438e84f4576b3a9bf76f86e"
	[rkbin]="1d3c61008fa823936ae7a59615393f8294b64456"
	[wifi-rtl8852bs]="35d3e2660fd912c36777cc50dd43b3fbc805d56a"
	[firmware]="f50a2a21bcdb77a562b3976930c5c6b521a1df08"
)
for component in "${!expected_commits[@]}"; do
	actual="$(git -C "${source_root}/${component}" rev-parse HEAD 2>/dev/null)" ||
		fail "${component} 不是可核對的 Git 工作樹"
	[[ "${actual}" == "${expected_commits[${component}]}" ]] ||
		fail "${component} 提交不符：${actual}"
done

output_parent="$(dirname "${output_dir}")"
mkdir -p "${output_parent}"
stage="$(mktemp -d "${output_parent}/.cm5pro-components.XXXXXX")"
cleanup_stage() {
	find "${stage}" -type f -delete 2>/dev/null || true
	find "${stage}" -depth -type d -empty -delete 2>/dev/null || true
}
trap cleanup_stage EXIT

python3 - "${config}" "${source_root}" "${stage}" <<'PY'
import hashlib
import json
from pathlib import Path
import shutil
import sys

config_path = Path(sys.argv[1])
source_root = Path(sys.argv[2])
stage = Path(sys.argv[3])
config = json.loads(config_path.read_text(encoding="utf-8"))
evidence = config["component_build_evidence"]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


manifest_artifacts = {}
for name, item in evidence["artifacts"].items():
    source = source_root / item["source_component"] / item["source_relative"]
    target = stage / item["output_relative"]
    if not source.is_file():
        raise SystemExit(f"CM5 Pro 缺少元件：{name}")
    if source.stat().st_size != item["size"] or sha256(source) != item["sha256"]:
        raise SystemExit(f"CM5 Pro 元件內容不符：{name}")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    manifest_artifacts[name] = {
        "path": item["output_relative"],
        "size": item["size"],
        "sha256": item["sha256"],
    }

manifest = {
    "schema_version": 1,
    "status": "complete",
    "scope": "component-only",
    "candidate_level": "L1 元件候選",
    "source_commits": {
        "linux": config["linux_commit"],
        "uboot": config["boards"]["bananapicm5pro"]["uboot_revision"],
        "rkbin": config["rkbin_commit"],
        "wifi-rtl8852bs": config["wifi_driver_commit"],
        "firmware": config["firmware_commit"],
    },
    "artifacts": manifest_artifacts,
    "rootfs_image_built": False,
    "hardware_tested": False,
    "public_distribution_approved": False,
}
(stage / "COMPONENT_BUILD_MANIFEST.json").write_text(
    json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8",
)
PY

mv "${stage}" "${output_dir}"
trap - EXIT
echo "CM5 Pro 可攜元件證據已匯出：${output_dir}"
