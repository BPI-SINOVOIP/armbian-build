#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
validation_config="${repo_dir}/config/validation/bananapi-realtek-rtd1296-w2-legacy.json"
output_dir="${W2_COMPONENT_OUTPUT_DIR:-${repo_dir}/output/components/2026.08/bananapi-realtek-rtd1296-w2-legacy}"

for command in cut fdtget grep python3 sha256sum stat strings tar; do
	command -v "${command}" >/dev/null || {
		echo "W2 元件驗證缺少命令：${command}" >&2
		exit 1
	}
done

python3 "${repo_dir}/tools/check-bananapi-realtek-w2-source-policy.py" \
	"${validation_config}"
[[ ! -e "${output_dir}/source" && ! -e "${output_dir}/build" ]] || {
	echo "W2 元件證據不得包含原始碼或建置樹。" >&2
	exit 1
}
grep -Fq '"status": "complete"' "${output_dir}/COMPONENT_BUILD_STATUS.json" || {
	echo "W2 元件建置狀態不是 complete。" >&2
	exit 1
}
grep -Fq '"uboot_rebuild_hash_match": true' \
	"${output_dir}/COMPONENT_BUILD_STATUS.json" || {
	echo "W2 U-Boot 重建雜湊守門未通過。" >&2
	exit 1
}

python3 - "${validation_config}" "${output_dir}/COMPONENTS.tsv" <<'PY'
import csv
import json
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    expected = json.load(stream)["component_build_evidence"]["artifacts"]
with open(sys.argv[2], encoding="utf-8", newline="") as stream:
    rows = csv.DictReader(stream, delimiter="\t")
    actual = {
        row["產物"]: {"size": int(row["大小"]), "sha256": row["SHA-256"]}
        for row in rows
    }
if actual != expected:
    raise SystemExit("W2 元件清單與 Git 內驗證契約不一致。")
PY

while IFS=$'\t' read -r name expected_size expected_sha256; do
	[[ "${name}" == "產物" ]] && continue
	path="${output_dir}/${name}"
	[[ -f "${path}" ]] || {
		echo "W2 元件缺少產物：${name}" >&2
		exit 1
	}
	[[ "$(stat -c %s "${path}")" == "${expected_size}" ]] || {
		echo "W2 元件大小不符：${name}" >&2
		exit 1
	}
	[[ "$(sha256sum "${path}" | cut -d' ' -f1)" == \
		"${expected_sha256}" ]] || {
		echo "W2 元件雜湊不符：${name}" >&2
		exit 1
	}
done <"${output_dir}/COMPONENTS.tsv"

python3 - "${validation_config}" "${output_dir}/SOURCE_ASSETS.tsv" <<'PY'
import csv
import json
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    config = json.load(stream)
with open(sys.argv[2], encoding="utf-8", newline="") as stream:
    rows = {row["路徑"]: row for row in csv.DictReader(stream, delimiter="\t")}

expected = {
    config["linux_license_path"]: config["linux_license_sha256"],
    config["uboot_license_path"]: config["uboot_license_sha256"],
    config["build_toolchain"]["path"]: config["build_toolchain"]["sha256"],
    config["build_toolchain"]["manifest_path"]: config["build_toolchain"]["manifest_sha256"],
}
for section in (
    "linked_prebuilt_assets",
    "runtime_prebuilt_assets",
    "excluded_source_assets",
):
    expected.update(
        {path: item["sha256"] for path, item in config[section].items()}
    )
if set(rows) != set(expected):
    raise SystemExit("W2 來源資產清單路徑集合不符。")
for path, digest in expected.items():
    if rows[path]["SHA-256"] != digest:
        raise SystemExit(f"W2 來源資產雜湊不符：{path}")
PY

python3 - "${validation_config}" \
	"${output_dir}/UBOOT_LINKED_PREBUILT_ASSETS.tsv" <<'PY'
import csv
import json
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    expected = set(json.load(stream)["linked_prebuilt_assets"])
with open(sys.argv[2], encoding="utf-8", newline="") as stream:
    rows = list(csv.DictReader(stream, delimiter="\t"))
if {row["路徑"] for row in rows} != expected:
    raise SystemExit("W2 U-Boot 連結資產集合不符。")
if any(row["連結映射命中"] != "true" for row in rows):
    raise SystemExit("W2 U-Boot 有預建資產未出現在連結映射。")
PY

dtb="${output_dir}/rtd-1296-bananapi-w2-2GB.dtb"
[[ "$(fdtget -t s "${dtb}" / model)" == "Banana Pi BPI-W2" ]] || {
	echo "W2 Linux DTB model 不符。" >&2
	exit 1
}
compatible="$(fdtget -t s "${dtb}" / compatible)"
for expected in bananapi,bpi-w2 realtek,rtd1296; do
	[[ " ${compatible} " == *" ${expected} "* ]] || {
		echo "W2 Linux DTB 缺少 compatible：${expected}" >&2
		exit 1
	}
done

for node in \
	/sdmmc@98010400 \
	/emmc@98012000 \
	/sata@9803F000 \
	/gmac@98016000 \
	/pcie@9804E000 \
	/pcie2@9803B000 \
	/rtk_dwc3_drd@98013200 \
	/rtk_dwc3_u2host@98013E00 \
	/rtk_dwc3_u3host@98013E00 \
	/hdmitx@9800D000 \
	/dptx@9803D000 \
	/rtk_misc_gpio@9801b100 \
	/rtk_iso_gpio@98007100 \
	/i2c@0x98007D00 \
	/i2c@0x98007C00 \
	/i2c@0x9801B700 \
	/i2c@0x9801B900 \
	/i2c@0x9801BA00 \
	/i2c@0x9801BB00 \
	/spi@9801BD00 \
	/pwm@980070D0; do
	[[ "$(fdtget -t s "${dtb}" "${node}" status)" == "okay" ]] || {
		echo "W2 Linux DTB 節點未啟用：${node}" >&2
		exit 1
	}
done
[[ "$(fdtget -t s "${dtb}" /hdmirx@98034000 status)" == "disabled" ]] || {
	echo "W2 Linux DTB HDMI RX 狀態不是 disabled。" >&2
	exit 1
}
[[ "$(fdtget -t s "${dtb}" \
	/rtk_dwc3_drd@98013200/dwc3_drd@98020000 dr_mode)" == \
	"peripheral" ]] || {
	echo "W2 USB DRD 模式不是 peripheral。" >&2
	exit 1
}

while IFS='=' read -r option value; do
	[[ "${option}" == CONFIG_* ]] || continue
	grep -Fqx "${option}=${value}" "${output_dir}/linux.config" || {
		echo "W2 Linux 設定不符：${option}=${value}" >&2
		exit 1
	}
done < <(
	python3 - "${validation_config}" <<'PY'
import json
import sys
with open(sys.argv[1], encoding="utf-8") as stream:
    options = json.load(stream)["common_kernel_options"]
for name in sorted(options):
    print(f"{name}={options[name]}")
PY
)

while IFS= read -r fragment; do
	grep -aFq "${fragment}" "${output_dir}/u-boot.bin" || {
		echo "W2 U-Boot 缺少識別字串：${fragment}" >&2
		exit 1
	}
done < <(
	python3 - "${validation_config}" <<'PY'
import json
import sys
with open(sys.argv[1], encoding="utf-8") as stream:
    for value in json.load(stream)["boards"]["bananapiw2"]["uboot_required_binary_strings"]:
        print(value)
PY
)

grep -Fqx 'root=LABEL=BPI-ROOT rw rootfstype=ext4 rootwait' \
	"${output_dir}/uEnv.txt" || {
	echo "W2 uEnv 根檔案系統標籤不符。" >&2
	exit 1
}
if grep -Eq '^root=/dev/(mmcblk|sd)' "${output_dir}/uEnv.txt"; then
	echo "W2 uEnv 仍含硬編碼根裝置。" >&2
	exit 1
fi

module_listing="$(tar -tJf "${output_dir}/linux-modules.tar.xz")"
grep -q '^lib/modules/' <<<"${module_listing}" || {
	echo "W2 modules 封裝沒有 lib/modules。" >&2
	exit 1
}

echo "W2 元件唯讀驗證通過；這不代表實機或公開發布通過。"
