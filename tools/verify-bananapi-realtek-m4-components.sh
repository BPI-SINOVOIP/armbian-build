#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
validation_config="${repo_dir}/config/validation/bananapi-realtek-rtd1395-m4-legacy.json"
output_dir="${M4_COMPONENT_OUTPUT_DIR:-${repo_dir}/output/components/2026.08/bananapi-realtek-rtd1395-m4-legacy}"

for command in fdtget grep python3 sha256sum stat tar xz; do
	command -v "${command}" >/dev/null || {
		echo "M4 元件驗證缺少命令：${command}" >&2
		exit 1
	}
done

python3 "${repo_dir}/tools/check-bananapi-realtek-m4-source-policy.py" \
	"${validation_config}"
[[ -d "${output_dir}" ]] || {
	echo "M4 可攜元件證據目錄不存在：${output_dir}" >&2
	exit 1
}

for forbidden in .git linux-rtk u-boot-rtk toolchains source stage; do
	if find "${output_dir}" -mindepth 1 -maxdepth 3 -type d -name "${forbidden}" -print -quit | grep -q .; then
		echo "M4 可攜證據不得包含原始碼或建置樹：${forbidden}" >&2
		exit 1
	fi
done

python3 - "${validation_config}" "${output_dir}" <<'PY'
import csv
import hashlib
import json
from pathlib import Path
import sys

config = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
output = Path(sys.argv[2])
evidence = config["component_build_evidence"]
for name, expected in evidence["artifacts"].items():
    path = output / name
    if not path.is_file():
        raise SystemExit(f"M4 元件缺失：{name}")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if path.stat().st_size != expected["size"]:
        raise SystemExit(f"M4 元件大小不符：{name}")
    if digest != expected["sha256"]:
        raise SystemExit(f"M4 元件雜湊不符：{name}")

status = json.loads((output / "COMPONENT_BUILD_STATUS.json").read_text(encoding="utf-8"))
if status["status"] != "complete":
    raise SystemExit("M4 元件建置狀態未完成")
if status["source_revision"] != config["uboot_commit"]:
    raise SystemExit("M4 元件來源提交不符")
if status["full_rootfs_image_built"] is not False:
    raise SystemExit("M4 元件狀態不得冒充完整映像")
if status["uboot_rebuild_hash_match"] is not True:
    raise SystemExit("M4 U-Boot 重建雜湊不一致")
if status["uboot_rebuild_sha256"] != evidence["uboot_rebuild_sha256"]:
    raise SystemExit("M4 U-Boot 狀態與契約雜湊不符")
for key in (
    "completed_utc",
    "source_revision",
    "uboot_rebuild_hash_match",
    "uboot_rebuild_sha256",
    "toolchain_gcc_sha256",
    "work_size_kib",
    "uboot_warning_count",
    "linux_warning_count",
    "linked_unrebuilt_source_asset_count",
    "conditional_unlinked_prebuilt_asset_count",
):
    if status[key] != evidence[key]:
        raise SystemExit(f"M4 元件狀態與契約欄位不符：{key}")
if status["source_date_epoch"] != config["source_date_epoch"]:
    raise SystemExit("M4 元件狀態的來源時間基準不符")
if status["work_size_kib"] > 10 * 1024 * 1024:
    raise SystemExit("M4 元件工作目錄超過契約上限")

manifest = {}
for line in (output / "COMPONENTS.tsv").read_text(encoding="utf-8").splitlines()[1:]:
    name, size, digest = line.split("\t")
    manifest[name] = {"size": int(size), "sha256": digest}
if manifest != evidence["artifacts"]:
    raise SystemExit("M4 元件清單與機器契約不一致")

expected_source_assets = {
    config["linux_license_path"]: config["linux_license_sha256"],
    config["uboot_license_path"]: config["uboot_license_sha256"],
    config["build_toolchain"]["path"]: config["build_toolchain"]["sha256"],
    config["build_toolchain"]["manifest_path"]: config["build_toolchain"]["manifest_sha256"],
}
for section in (
    "conditional_unlinked_prebuilt_assets",
    "linked_unrebuilt_source_assets",
    "runtime_prebuilt_assets",
    "excluded_source_assets",
):
    for path, metadata in config[section].items():
        expected_source_assets[path] = metadata["sha256"]
with (output / "SOURCE_ASSETS.tsv").open(encoding="utf-8", newline="") as stream:
    source_rows = list(csv.DictReader(stream, delimiter="\t"))
source_assets = {row["路徑"]: row for row in source_rows}
if len(source_assets) != len(source_rows) or set(source_assets) != set(expected_source_assets):
    raise SystemExit("M4 來源資產清單集合或唯一性不符")
for path, expected_digest in expected_source_assets.items():
    row = source_assets[path]
    if int(row["大小"]) <= 0 or row["SHA-256"] != expected_digest:
        raise SystemExit(f"M4 來源資產清單內容不符：{path}")

with (output / "UBOOT_PREBUILT_INPUT_EVIDENCE.tsv").open(encoding="utf-8", newline="") as stream:
    prebuilt_rows = list(csv.DictReader(stream, delimiter="\t"))
prebuilt_assets = {row["路徑"]: row for row in prebuilt_rows}
expected_prebuilt = set(config["conditional_unlinked_prebuilt_assets"]) | set(
    config["linked_unrebuilt_source_assets"]
)
if len(prebuilt_assets) != len(prebuilt_rows) or set(prebuilt_assets) != expected_prebuilt:
    raise SystemExit("M4 U-Boot 預建輸入證據集合或唯一性不符")
for path in config["conditional_unlinked_prebuilt_assets"]:
    row = prebuilt_assets[path]
    observed = "/".join(
        row[key]
        for key in (
            "分類",
            "Makefile命中",
            "內容嵌入吻合",
            "實際連結命令命中",
            "連結映射命中",
            "本次重建",
            "進入候選",
        )
    )
    if observed != "條件式未連結預建資產/true/不適用/false/false/false/false":
        raise SystemExit(f"M4 U-Boot 未連結資產證據矛盾：{path}")
for path in config["linked_unrebuilt_source_assets"]:
    row = prebuilt_assets[path]
    observed = "/".join(
        row[key]
        for key in (
            "分類",
            "Makefile命中",
            "內容嵌入吻合",
            "實際連結命令命中",
            "連結映射命中",
            "本次重建",
            "進入候選",
        )
    )
    if observed != "已嵌入但未重建來源資產/true/true/true/true/false/true":
        raise SystemExit(f"M4 U-Boot 已嵌入資產證據矛盾：{path}")

kernel_options = {}
for line in (output / "linux.config").read_text(encoding="utf-8").splitlines():
    if line.startswith("CONFIG_") and "=" in line:
        key, value = line.split("=", 1)
        kernel_options[key] = value
for key, expected in config["common_kernel_options"].items():
    if kernel_options.get(key) != expected:
        raise SystemExit(f"M4 核心設定與機器契約不符：{key}")
PY

grep -Fqx 'root=LABEL=BPI-ROOT rw rootfstype=ext4 rootwait' "${output_dir}/uEnv.txt" || {
	echo "M4 uEnv.txt 未使用穩定根標籤。" >&2
	exit 1
}
grep -aFq 'U-Boot 2015.07' "${output_dir}/u-boot.bin" || {
	echo "M4 U-Boot 版本識別不符。" >&2
	exit 1
}
grep -aFq 'Mar 22 2024' "${output_dir}/u-boot.bin" || {
	echo "M4 U-Boot 未固定來源時間。" >&2
	exit 1
}

for entry in \
	'rtd-1395-bananapi-m4-1GB.dtb 0 40000000' \
	'rtd-1395-bananapi-m4-2GB.dtb 0 80000000'; do
	read -r dtb first_cell size_cell <<<"${entry}"
	[[ "$(fdtget -t s "${output_dir}/${dtb}" / model)" == "Banana Pi BPI-M4" ]] || {
		echo "M4 DTB model 不符：${dtb}" >&2
		exit 1
	}
	[[ "$(fdtget -t s "${output_dir}/${dtb}" / compatible)" == "bananapi,bpi-m4 realtek,rtd1395" ]] || {
		echo "M4 DTB compatible 不符：${dtb}" >&2
		exit 1
	}
	[[ "$(fdtget -t x "${output_dir}/${dtb}" /memory@0 reg)" == "${first_cell} ${size_cell}" ]] || {
		echo "M4 DTB 記憶體容量不符：${dtb}" >&2
		exit 1
	}
done

xz -t "${output_dir}/linux-modules.tar.xz"
module_list="$(tar -tJf "${output_dir}/linux-modules.tar.xz")"
module_count="$(grep -c '\.ko$' <<<"${module_list}" || true)"
if ((module_count < 1)); then
	echo "M4 modules 封裝沒有核心模組。" >&2
	exit 1
fi
grep -q '/8821cu\.ko$' <<<"${module_list}" || {
	echo "M4 modules 封裝缺少 RTL8821CU 驅動。" >&2
	exit 1
}

grep -Fq 'image/rtd1395/src/app/libbootload.o' \
	"${output_dir}/u-boot-link-command.txt" || {
	echo "M4 U-Boot 連結命令證據缺少 libbootload.o。" >&2
	exit 1
}

echo "M4 可攜元件、DTB、U-Boot、核心設定與 modules 唯讀驗證通過。"
