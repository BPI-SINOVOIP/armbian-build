#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
config="${VALIDATION_CONFIG:-${repo_dir}/config/validation/bananapi-spacemit-k3-sm10-current.json}"
component_root="${COMPONENT_ROOT:-${repo_dir}/output/components/2026.08/bananapi-spacemit-k3-sm10-current}"

for command in fdtget find python3; do
	command -v "${command}" >/dev/null || {
		echo "SM10 元件驗證缺少必要命令：${command}" >&2
		exit 1
	}
done

fail() {
	echo "SM10 元件驗證失敗：$*" >&2
	exit 1
}

[[ -f "${config}" ]] || fail "找不到驗證契約：${config}"
[[ -d "${component_root}" ]] || fail "找不到元件證據：${component_root}"
for required in COMPONENTS.tsv COMPONENT_STATUS.json source-evidence/PROJECTS.tsv \
	source-evidence/SOURCE_STATUS.json source-evidence/resolved-manifest.xml; do
	[[ -f "${component_root}/${required}" ]] || fail "缺少元件證據：${required}"
done

# 可交接證據不得包含 SDK 來源樹；來源樹中可能存在測試或私用簽章金鑰。
[[ ! -e "${component_root}/src" && ! -e "${component_root}/build" ]] ||
	fail "可交接證據不得包含 SDK 原始碼或私鑰"
if find "${component_root}" -xdev -type f \
	\( -name '*.key' -o -name '*_prv.crt' \) -print -quit | grep -q .; then
	fail "可交接證據含有私鑰材料"
fi

python3 - "${config}" "${component_root}" <<'PY'
import csv
import hashlib
import json
from pathlib import Path
import sys

config_path = Path(sys.argv[1])
root = Path(sys.argv[2])
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
    "COMPONENT_STATUS.json": evidence["status_sha256"],
    "source-evidence/SOURCE_STATUS.json": evidence["source_status_sha256"],
    "source-evidence/PROJECTS.tsv": evidence["source_projects_sha256"],
    "source-evidence/resolved-manifest.xml": config["sdk"]["resolved_manifest_sha256"],
}
for relative, expected in fixed_files.items():
    actual = sha256(root / relative)
    if actual != expected:
        raise SystemExit(f"SM10 元件證據雜湊不符：{relative}")

status = json.loads((root / "COMPONENT_STATUS.json").read_text(encoding="utf-8"))
if status.get("status") != "complete":
    raise SystemExit("SM10 元件建置狀態不是 complete")
if status.get("hardware_validation") is not False:
    raise SystemExit("SM10 元件證據不得核准硬體聲明")
if status.get("public_distribution_approved") is not False:
    raise SystemExit("SM10 元件證據不得核准公開散布")
environment = status.get("build_environment", {})
if environment.get("container_image_id") != evidence["container_image_id"]:
    raise SystemExit("SM10 元件容器映像 ID 不符")
if environment.get("compiler_binary_sha256") != evidence["compiler_binary_sha256"]:
    raise SystemExit("SM10 元件編譯器雜湊不符")

with (root / "COMPONENTS.tsv").open(encoding="utf-8", newline="") as stream:
    rows = list(csv.DictReader(stream, delimiter="\t"))
actual = {row["artifact"]: row for row in rows}
if set(actual) != set(evidence["artifacts"]):
    raise SystemExit("SM10 元件集合與驗證契約不符")
for artifact, expected in evidence["artifacts"].items():
    row = actual[artifact]
    path = root / "artifacts" / artifact
    if not path.is_file():
        raise SystemExit(f"找不到 SM10 元件產物：{artifact}")
    checks = {
        "component": expected["component"],
        "source_revision": expected["source_revision"],
        "size": str(expected["size"]),
        "sha256": expected["sha256"],
    }
    for field, value in checks.items():
        if row[field] != value:
            raise SystemExit(f"SM10 元件清單欄位不符：{artifact}/{field}")
    if path.stat().st_size != expected["size"]:
        raise SystemExit(f"SM10 元件大小不符：{artifact}")
    if sha256(path) != expected["sha256"]:
        raise SystemExit(f"SM10 元件內容雜湊不符：{artifact}")

source_status = json.loads(
    (root / "source-evidence/SOURCE_STATUS.json").read_text(encoding="utf-8")
)
if source_status.get("status") != "complete" or not source_status.get(
    "all_projects_clean"
):
    raise SystemExit("SM10 來源證據未完成或來源不乾淨")
if source_status.get("project_count") != config["sdk"]["project_count"]:
    raise SystemExit("SM10 來源專案數量不符")
PY

dtb="${component_root}/artifacts/k3-bananapi-sm10.dtb"
[[ "$(fdtget "${dtb}" / model)" == "BananaPi BPI-SM10" ]] ||
	fail "SM10 DTB model 不符"
[[ "$(fdtget "${dtb}" / compatible)" == \
	"bananapi,bpi-sm10 spacemit,k3-com260" ]] || fail "SM10 DTB compatible 不符"

while IFS=$'\t' read -r option value; do
	grep -Fxq "${option}=${value}" "${component_root}/artifacts/linux.config" ||
		fail "Linux 組態不符：${option}=${value}"
done < <(python3 - "${config}" <<'PY'
import json, sys
for key, value in json.load(open(sys.argv[1], encoding="utf-8"))["common_kernel_options"].items():
    print(f"{key}\t{value}")
PY
)

while IFS= read -r option; do
	grep -Fxq "${option}" "${component_root}/artifacts/uboot.config" ||
		fail "U-Boot 組態不符：${option}"
done < <(python3 - "${config}" <<'PY'
import json, sys
print("\n".join(json.load(open(sys.argv[1], encoding="utf-8"))["boards"]["bananapism10"]["uboot_required_config_options"]))
PY
)

echo "BPI-SM10 元件唯讀證據驗證通過；不代表完整映像、實機或公開發布通過。"
