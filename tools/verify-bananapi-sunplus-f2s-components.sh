#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
validation_config="${repo_dir}/config/validation/bananapi-sunplus-sp7021-f2s-legacy.json"
output_dir="${F2S_COMPONENT_OUTPUT_DIR:-${repo_dir}/.tmp/bananapi-sunplus-f2s-component/output}"

for command in cut fdtget grep python3 sha256sum stat tar; do
	command -v "${command}" >/dev/null || {
		echo "F2S 元件驗證缺少命令：${command}" >&2
		exit 1
	}
done

python3 "${repo_dir}/tools/check-bananapi-sunplus-f2s-source-policy.py" \
	"${validation_config}"
grep -Fq '"status": "complete"' "${output_dir}/COMPONENT_BUILD_STATUS.json" || {
	echo "F2S 元件建置狀態不是 complete。" >&2
	exit 1
}
grep -Fq '"uboot_rebuild_hash_match": true' \
	"${output_dir}/COMPONENT_BUILD_STATUS.json" || {
	echo "F2S U-Boot 重建雜湊守門未通過。" >&2
	exit 1
}
expected_toolchain_sha256="$(python3 - "${validation_config}" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    print(
        json.load(stream)["component_build_evidence"]
        ["toolchain"]["gcc_sha256"]
    )
PY
)"
grep -Fq "\"toolchain_gcc_sha256\": \"${expected_toolchain_sha256}\"" \
	"${output_dir}/COMPONENT_BUILD_STATUS.json" || {
	echo "F2S 元件狀態缺少固定工具鏈雜湊。" >&2
	exit 1
}

while IFS=$'\t' read -r name expected_size expected_sha256; do
	[[ "${name}" == "產物" ]] && continue
	path="${output_dir}/${name}"
	[[ -f "${path}" ]] || {
		echo "F2S 元件缺少產物：${name}" >&2
		exit 1
	}
	[[ "$(stat -c %s "${path}")" == "${expected_size}" ]] || {
		echo "F2S 元件大小不符：${name}" >&2
		exit 1
	}
	[[ "$(sha256sum "${path}" | cut -d' ' -f1)" == "${expected_sha256}" ]] || {
		echo "F2S 元件雜湊不符：${name}" >&2
		exit 1
	}
done <"${output_dir}/COMPONENTS.tsv"

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
    raise SystemExit("F2S 元件清單與 Git 內驗證契約不一致。")
PY

python3 - "${validation_config}" "${output_dir}/SOURCE_ASSETS.tsv" <<'PY'
import csv
import json
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    config = json.load(stream)
with open(sys.argv[2], encoding="utf-8", newline="") as stream:
    rows = {row["路徑"]: row for row in csv.DictReader(stream, delimiter="\t")}

expected_paths = set(config["source_assets"])
expected_paths.update(
    (config["linux_license_path"], config["uboot_license_path"])
)
if set(rows) != expected_paths:
    raise SystemExit("F2S 來源資產清單的路徑集合不符。")

for path, expected in config["source_assets"].items():
    row = rows[path]
    if int(row["大小"]) != expected["size"] or row["SHA-256"] != expected["sha256"]:
        raise SystemExit(f"F2S 來源資產清單不符：{path}")
    if row["解壓大小"] != str(expected.get("uncompressed_size", "-")):
        raise SystemExit(f"F2S 來源資產解壓大小不符：{path}")
    if row["解壓 SHA-256"] != expected.get("uncompressed_sha256", "-"):
        raise SystemExit(f"F2S 來源資產解壓雜湊不符：{path}")

for prefix in ("linux", "uboot"):
    path = config[f"{prefix}_license_path"]
    if rows[path]["SHA-256"] != config[f"{prefix}_license_sha256"]:
        raise SystemExit(f"F2S 授權證據清單不符：{path}")
PY

dtb="${output_dir}/sp7021-bpi-f2s.dtb"
[[ "$(fdtget -t s "${dtb}" / model)" == "Banana Pi BPI-F2S" ]] || {
	echo "F2S Linux DTB model 不符。" >&2
	exit 1
}
compatible="$(fdtget -t s "${dtb}" / compatible)"
for expected in sinovoip,bpi-f2s sunplus,sp7021-achip; do
	[[ " ${compatible} " == *" ${expected} "* ]] || {
		echo "F2S Linux DTB 缺少 compatible：${expected}" >&2
		exit 1
	}
done

for fragment in 'Banana Pi BPI-F2S' 'sinovoip,bpi-f2s'; do
	grep -aFq "${fragment}" "${output_dir}/u-boot.img" || {
		echo "F2S U-Boot 缺少身分字串：${fragment}" >&2
		exit 1
	}
done
if grep -aFq 'SP7021/CA7/BPI-F2S' "${output_dir}/u-boot.img"; then
	echo "F2S U-Boot 仍含舊板級身分。" >&2
	exit 1
fi

while IFS='=' read -r option value; do
	[[ "${option}" == CONFIG_* ]] || continue
	grep -Fqx "${option}=${value}" "${output_dir}/linux.config" || {
		echo "F2S Linux 設定不符：${option}=${value}" >&2
		exit 1
	}
done < <(python3 - "${validation_config}" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    options = json.load(stream)["common_kernel_options"]
for name in sorted(options):
    print(f"{name}={options[name]}")
PY
)

module_listing="$(tar -tJf "${output_dir}/linux-modules.tar.xz")"
grep -q '^lib/modules/' <<<"${module_listing}" || {
	echo "F2S modules 封裝沒有 lib/modules。" >&2
	exit 1
}

echo "F2S 元件唯讀驗證通過；這不代表實機或公開發布通過。"
