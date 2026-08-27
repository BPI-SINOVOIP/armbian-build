#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
config="${repo_dir}/config/validation/bananapi-rockchip-rk3576-cm5pro-vendor.json"
component_root="${CM5PRO_COMPONENT_EVIDENCE_ROOT:-${repo_dir}/output/components/2026.08/bananapi-rockchip-rk3576-cm5pro-vendor}"
manifest="${component_root}/COMPONENT_BUILD_MANIFEST.json"

for command in fdtget file modinfo python3; do
	command -v "${command}" >/dev/null || {
		echo "CM5 Pro 元件驗證缺少必要命令：${command}" >&2
		exit 1
	}
done

fail() {
	echo "CM5 Pro 元件驗證失敗：$*" >&2
	exit 1
}

[[ -f "${config}" && -f "${manifest}" ]] || fail "找不到驗證契約或元件清單"
[[ ! -e "${component_root}/source" && ! -e "${component_root}/build" ]] ||
	fail "可攜證據不得包含來源樹或建置樹"

python3 - "${config}" "${manifest}" "${component_root}" <<'PY'
import hashlib
import json
from pathlib import Path
import sys

config = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
manifest = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
root = Path(sys.argv[3])
evidence = config["component_build_evidence"]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


if sha256(Path(sys.argv[2])) != evidence["portable_manifest_sha256"]:
    raise SystemExit("CM5 Pro 可攜元件清單雜湊不符")
if manifest.get("status") != "complete" or manifest.get("scope") != "component-only":
    raise SystemExit("CM5 Pro 元件清單狀態或範圍不符")
if manifest.get("candidate_level") != "L1 元件候選":
    raise SystemExit("CM5 Pro 元件清單層級不符")
for field in ("rootfs_image_built", "hardware_tested", "public_distribution_approved"):
    if manifest.get(field) is not False:
        raise SystemExit(f"CM5 Pro 元件清單不得核准：{field}")

expected_commits = {
    "linux": config["linux_commit"],
    "uboot": config["boards"]["bananapicm5pro"]["uboot_revision"],
    "rkbin": config["rkbin_commit"],
    "wifi-rtl8852bs": config["wifi_driver_commit"],
    "firmware": config["firmware_commit"],
}
if manifest.get("source_commits") != expected_commits:
    raise SystemExit("CM5 Pro 元件來源提交不符")
if len(manifest.get("artifacts", {})) != evidence["portable_artifact_count"]:
    raise SystemExit("CM5 Pro 可攜元件數量不符")

for name, item in evidence["artifacts"].items():
    expected = {
        "path": item["output_relative"],
        "size": item["size"],
        "sha256": item["sha256"],
    }
    if manifest["artifacts"].get(name) != expected:
        raise SystemExit(f"CM5 Pro 元件清單欄位不符：{name}")
    path = root / expected["path"]
    if not path.is_file() or path.stat().st_size != expected["size"]:
        raise SystemExit(f"CM5 Pro 元件大小不符：{name}")
    if sha256(path) != expected["sha256"]:
        raise SystemExit(f"CM5 Pro 元件雜湊不符：{name}")
PY

linux_dtb="${component_root}/artifacts/rk3576-bananapi-cm5-pro.dtb"
uboot_dtb="${component_root}/artifacts/u-boot.dtb"
wifi_module="${component_root}/artifacts/8852bs.ko"
[[ "$(fdtget -t s "${linux_dtb}" / model)" == "Banana Pi CM5 Pro" ]] || fail "Linux DTB model 不符"
[[ "$(fdtget -t s "${linux_dtb}" / compatible)" == \
	"bananapi,bpi-cm5-pro armsom,cm5-io armsom,cm5 rockchip,rk3576" ]] || fail "Linux DTB compatible 不符"
[[ "$(fdtget -t s "${uboot_dtb}" / model)" == "Banana Pi CM5 Pro" ]] || fail "U-Boot DTB model 不符"
file "${wifi_module}" | grep -q 'ELF 64-bit.*ARM aarch64' || fail "RTL8852BS 模組架構不符"
[[ "$(modinfo -F license "${wifi_module}")" == "GPL" ]] || fail "RTL8852BS 模組授權欄位不符"
[[ "$(modinfo -F vermagic "${wifi_module}")" == 6.1.115\ SMP\ mod_unload\ aarch64 ]] || fail "RTL8852BS 模組核心版本不符"

echo "CM5 Pro 元件唯讀證據驗證通過；不代表完整映像、實機或公開發布通過。"
