#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
config="${repo_dir}/config/validation/bananapi-rockchip-rk3588-aim7-vendor.json"
component_root="${AIM7_EVIDENCE_ROOT:-${repo_dir}/output/components/2026.08/bananapi-rockchip-rk3588-aim7-vendor}"
manifest="${component_root}/COMPONENT_BUILD_MANIFEST.json"

for command in fdtget python3; do
	command -v "${command}" >/dev/null || {
		echo "BPI-AIM7 元件驗證缺少必要命令：${command}" >&2
		exit 1
	}
done

fail() {
	echo "BPI-AIM7 元件驗證失敗：$*" >&2
	exit 1
}

[[ -f "${config}" && -f "${manifest}" ]] || fail "找不到驗證契約或元件清單"
[[ ! -e "${component_root}/source" && ! -e "${component_root}/build" ]] ||
	fail "可攜證據不得包含原始碼或建置樹"

python3 - "${config}" "${manifest}" "${component_root}" <<'PY'
import hashlib
import json
from pathlib import Path
import sys

config_path = Path(sys.argv[1])
manifest_path = Path(sys.argv[2])
root = Path(sys.argv[3])
config = json.loads(config_path.read_text(encoding="utf-8"))
manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
evidence = config["component_build_evidence"]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


if sha256(manifest_path) != evidence["portable_manifest_sha256"]:
    raise SystemExit("BPI-AIM7 可攜元件清單雜湊不符")
if manifest.get("status") != "complete" or manifest.get("scope") != "component-only":
    raise SystemExit("BPI-AIM7 元件清單狀態或範圍不符")
if manifest.get("candidate_level") != "L1 元件候選":
    raise SystemExit("BPI-AIM7 元件清單層級不符")
for field in ("rootfs_image_built", "hardware_tested", "public_distribution_approved"):
    if manifest.get(field) is not False:
        raise SystemExit(f"BPI-AIM7 元件清單不得核准：{field}")
if manifest.get("armbian_patch_stack_complete") is not False:
    raise SystemExit("BPI-AIM7 元件清單不得宣稱完整 Armbian 修補佇列通過")

expected_environment = {
    "source_date_epoch": evidence["source_date_epoch"],
    "build_user": evidence["build_user"],
    "build_host": evidence["build_host"],
    "cross_compile": "aarch64-linux-gnu-",
}
if manifest.get("reproducible_environment") != expected_environment:
    raise SystemExit("BPI-AIM7 可重現建置環境不符")
expected_commits = {
    "linux": config["linux_commit"],
    "uboot": config["boards"]["bananapiaim7"]["uboot_revision"],
    "rkbin": config["rkbin_commit"],
}
if manifest.get("source_commits") != expected_commits:
    raise SystemExit("BPI-AIM7 元件來源提交不符")

expected = {
    "linux_dtb": ("artifacts/rk3588-bananapi-aim7.dtb", "linux_dtb"),
    "uboot_spl": ("artifacts/u-boot-spl.bin", "uboot_spl"),
    "uboot_dtb": ("artifacts/u-boot.dtb", "uboot_dtb"),
    "uboot_fit": ("artifacts/u-boot.itb", "uboot_itb"),
    "idbloader": ("artifacts/idbloader.img", "idbloader"),
}
if len(manifest["artifacts"]) != evidence["portable_artifact_count"]:
    raise SystemExit("BPI-AIM7 可攜元件數量不符")
for name, (relative, evidence_name) in expected.items():
    item = manifest["artifacts"].get(name)
    wanted = {
        "path": relative,
        "size": evidence[f"{evidence_name}_size"],
        "sha256": evidence[f"{evidence_name}_sha256"],
    }
    if item != wanted:
        raise SystemExit(f"BPI-AIM7 元件清單欄位不符：{name}")
    path = root / relative
    if not path.is_file() or path.stat().st_size != wanted["size"]:
        raise SystemExit(f"BPI-AIM7 元件大小不符：{name}")
    if sha256(path) != wanted["sha256"]:
        raise SystemExit(f"BPI-AIM7 元件內容雜湊不符：{name}")

license_item = manifest["artifacts"].get("rkbin_license")
license_expected = {
    "path": "rkbin.LICENSE.TXT",
    "size": 6585,
    "sha256": config["rkbin_blobs"]["LICENSE.TXT"],
}
if license_item != license_expected or sha256(root / "rkbin.LICENSE.TXT") != license_expected["sha256"]:
    raise SystemExit("BPI-AIM7 RKBin 授權檔不符")
PY

linux_dtb="${component_root}/artifacts/rk3588-bananapi-aim7.dtb"
uboot_dtb="${component_root}/artifacts/u-boot.dtb"
[[ "$(fdtget -t s "${linux_dtb}" / model)" == "Banana Pi AIM7" ]] || fail "Linux DTB model 不符"
[[ "$(fdtget -t s "${linux_dtb}" / compatible)" == \
	"bananapi,bpi-aim7 armsom,aim7-io rockchip,rk3588" ]] || fail "Linux DTB compatible 不符"
[[ "$(fdtget -t s "${uboot_dtb}" / model)" == "Banana Pi AIM7" ]] || fail "U-Boot DTB model 不符"

echo "BPI-AIM7 元件唯讀證據驗證通過；不代表完整映像、實機或公開發布通過。"
