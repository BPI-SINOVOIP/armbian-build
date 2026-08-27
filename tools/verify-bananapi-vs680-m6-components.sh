#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
validation_config="${VALIDATION_CONFIG:-${repo_dir}/config/validation/bananapi-vs680-m6-legacy.json}"
output_dir="${COMPONENT_OUTPUT_DIR:-${repo_dir}/output/components/2026.08/bananapi-vs680-m6-legacy}"
package_dir="${COMPONENT_PACKAGE_DIR:-${repo_dir}/output/debs}"

for command in cut find grep python3 sha256sum stat; do
	command -v "${command}" >/dev/null || {
		echo "元件驗證缺少必要命令：${command}" >&2
		exit 1
	}
done

fail() {
	echo "元件驗證失敗：$*" >&2
	exit 1
}

[[ -f "${validation_config}" ]] || fail "找不到驗證契約"
for required in COMPLETION_STATUS.json COMPONENT_VERIFICATION.json COMPONENTS.tsv \
	logs/uboot.log logs/kernel.log; do
	[[ -f "${output_dir}/${required}" ]] || fail "缺少元件證據：${required}"
done

"${repo_dir}/tools/verify-bananapi-vs680-m6-sources.sh"

python3 - "${validation_config}" "${output_dir}" "${package_dir}" <<'PY'
import csv
import hashlib
import json
from pathlib import Path
import sys

config_path = Path(sys.argv[1])
output_dir = Path(sys.argv[2])
package_dir = Path(sys.argv[3])
config = json.loads(config_path.read_text(encoding="utf-8"))
evidence = config["component_build_evidence"]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


fixed_files = {
    "COMPONENTS.tsv": evidence["manifest_sha256"],
    "COMPONENT_VERIFICATION.json": evidence["verification_sha256"],
    "logs/uboot.log": evidence["uboot_log_sha256"],
    "logs/kernel.log": evidence["kernel_log_sha256"],
}
for relative, expected in fixed_files.items():
    actual = sha256(output_dir / relative)
    if actual != expected:
        raise SystemExit(f"M6 元件證據雜湊不符：{relative}")

completion = json.loads(
    (output_dir / "COMPLETION_STATUS.json").read_text(encoding="utf-8")
)
verification = json.loads(
    (output_dir / "COMPONENT_VERIFICATION.json").read_text(encoding="utf-8")
)
if completion.get("status") != "complete":
    raise SystemExit("M6 元件建置狀態不是 complete")
if completion.get("source_commit") != evidence["source_commit"]:
    raise SystemExit("M6 元件建置來源提交不符")
if verification.get("status") != "complete" or not verification.get(
    "component_build_verified"
):
    raise SystemExit("M6 元件內容驗證未完成")
for key in ("dtb_sha256", "uboot_sha256"):
    if verification.get(key) != evidence[key]:
        raise SystemExit(f"M6 元件驗證欄位不符：{key}")
if verification.get("hardware_claims_allowed") is not False:
    raise SystemExit("M6 元件證據不得核准硬體聲明")
if verification.get("public_release_allowed") is not False:
    raise SystemExit("M6 元件證據不得核准公開發布")

with (output_dir / "COMPONENTS.tsv").open(
    encoding="utf-8", newline=""
) as stream:
    rows = list(csv.DictReader(stream, delimiter="\t"))
actual_components = {row["component"]: row for row in rows}
if set(actual_components) != set(evidence["packages"]):
    raise SystemExit("M6 元件集合與驗證契約不符")
for component, expected in evidence["packages"].items():
    row = actual_components[component]
    if row["sha256"] != expected:
        raise SystemExit(f"M6 元件清單雜湊不符：{component}")
    package = package_dir / row["filename"]
    if not package.is_file():
        raise SystemExit(f"找不到 M6 元件套件：{package}")
    if package.stat().st_size != int(row["size"]):
        raise SystemExit(f"M6 元件套件大小不符：{component}")
    if sha256(package) != expected:
        raise SystemExit(f"M6 元件套件內容雜湊不符：{component}")
PY

echo "BPI-M6 元件唯讀證據驗證通過；不代表實機或公開發布通過。"
