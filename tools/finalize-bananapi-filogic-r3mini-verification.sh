#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
policy="${VALIDATION_CONFIG:-${repo_dir}/config/validation/bananapi-filogic-mt7986-r3mini-current.json}"
output_dir="${OUTPUT_DIR:-${repo_dir}/output/images/2026.08/bananapi-filogic-mt7986-r3mini-emmc-trixie-current-cli}"
evidence="${output_dir}/UBOOT_PAYLOAD_EVIDENCE.tsv"
status_file="${1:-}"

[[ "${status_file}" == "${output_dir}/VERIFICATION_STATUS.json.partial" ]] || {
	echo "R3 Mini 收尾器只接受共用驗證器的暫存狀態" >&2
	exit 1
}
[[ -s "${policy}" && -s "${evidence}" && -s "${status_file}" ]] || {
	echo "R3 Mini 缺少政策、U-Boot 載荷或驗證狀態證據" >&2
	exit 1
}

python3 - "${policy}" "${evidence}" "${status_file}" <<'PY'
import csv
import json
import os
import re
import sys

policy_path, evidence_path, status_path = sys.argv[1:]
with open(policy_path, encoding="utf-8") as stream:
    policy = json.load(stream)
with open(status_path, encoding="utf-8") as stream:
    status = json.load(stream)

states = {
    "L1 元件候選": {
        "scope": "internal-component-only",
        "evidence_level": "L1",
        "full_image_built": False,
        "component_validation_only": True,
    },
    "L2 內部軟體候選": {
        "scope": "internal-l2",
        "evidence_level": "L2",
        "full_image_built": True,
        "component_validation_only": False,
    },
}
state = states.get(policy.get("candidate_level"))
if state is None:
    raise SystemExit("R3 Mini 候選層級不受支援")
if policy.get("allowed_evidence_levels") != ["L1", "L2"]:
    raise SystemExit("R3 Mini 允許的證據層級不符")
if policy.get("candidate_scope") != state["scope"]:
    raise SystemExit("R3 Mini 候選範圍與層級不一致")
if policy.get("full_rootfs_image_built") is not state["full_image_built"]:
    raise SystemExit("R3 Mini 完整映像狀態與候選層級不一致")
release_policy = policy.get("release_gate", {})
if (
    policy.get("public_release_authorized") is not False
    or policy.get("hardware_claims_allowed") is not False
    or policy.get("hardware_validation_completed") is not False
    or release_policy.get("status") != "blocked"
    or release_policy.get("public_release_authorized") is not False
    or release_policy.get("hardware_claims_allowed") is not False
    or release_policy.get("required_blockers") != policy.get("public_release_blockers")
):
    raise SystemExit("R3 Mini 公開發布或硬體聲明阻擋不完整")
if (
    release_policy.get("full_image_built") is not state["full_image_built"]
    or release_policy.get("component_validation_only")
    is not state["component_validation_only"]
):
    raise SystemExit("R3 Mini 發布守門狀態與候選層級不一致")
if status.get("status") != "complete" or status.get("evidence_level") != state["evidence_level"]:
    raise SystemExit("R3 Mini 驗證狀態不得越級")

board = policy["boards"]["bananapir3mini"]


def parse_assignments(values: list[str], label: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for value in values:
        name, separator, assigned = value.partition("=")
        if not separator or not name or name in result:
            raise SystemExit(f"R3 Mini {label}格式錯誤或重複：{value}")
        result[name] = assigned
    return result


minimums = parse_assignments(board["uboot_payload_minimum_sizes"], "載荷下限")
maximums = parse_assignments(board["uboot_payload_maximum_sizes"], "載荷上限")
digests = parse_assignments(board["uboot_payload_sha256"], "載荷雜湊")
expected: dict[str, dict[str, object]] = {}
for specification in board["uboot_payloads"]:
    name, separator, offset_text = specification.partition("@")
    if not separator or not offset_text.isdigit() or name in expected:
        raise SystemExit(f"R3 Mini 映像載荷規格錯誤或重複：{specification}")
    expected[name] = {"placement": "image", "offset": int(offset_text)}
for name in board["uboot_package_only_payloads"]:
    if not name or name in expected:
        raise SystemExit(f"R3 Mini 套件載荷規格錯誤或重複：{name}")
    expected[name] = {"placement": "package-only", "offset": None}
if set(expected) != set(minimums) or set(expected) != set(maximums) or set(expected) != set(digests):
    raise SystemExit("R3 Mini 載荷、上下限與雜湊清單不一致")

with open(evidence_path, newline="", encoding="utf-8") as stream:
    reader = csv.DictReader(stream, delimiter="\t")
    required_header = ["board", "payload", "placement", "offset", "size", "sha256"]
    if reader.fieldnames != required_header:
        raise SystemExit("R3 Mini U-Boot 載荷證據欄位不符")
    rows = list(reader)
if len(rows) != len(expected):
    raise SystemExit("R3 Mini U-Boot 載荷證據筆數不符")

verified_payloads = []
seen: set[str] = set()
for row in rows:
    name = row["payload"]
    if row["board"] != "bananapir3mini" or name not in expected or name in seen:
        raise SystemExit(f"R3 Mini 載荷證據包含錯誤板卡、未知或重複載荷：{name}")
    seen.add(name)
    specification = expected[name]
    if row["placement"] != specification["placement"]:
        raise SystemExit(f"R3 Mini 載荷位置類型不符：{name}")
    expected_offset = specification["offset"]
    actual_offset = None if row["offset"] == "-" else int(row["offset"])
    if actual_offset != expected_offset:
        raise SystemExit(f"R3 Mini 載荷偏移不符：{name}")
    try:
        size = int(row["size"])
        minimum = int(minimums[name])
        maximum = int(maximums[name])
    except ValueError as error:
        raise SystemExit(f"R3 Mini 載荷大小格式不符：{name}") from error
    if minimum <= 0 or maximum < minimum or not minimum <= size <= maximum:
        raise SystemExit(f"R3 Mini 載荷超出受控邊界：{name}")
    if not re.fullmatch(r"[0-9a-f]{64}", row["sha256"]):
        raise SystemExit(f"R3 Mini 載荷雜湊格式不符：{name}")
    if row["sha256"] != digests[name]:
        raise SystemExit(f"R3 Mini 載荷雜湊不符：{name}")
    verified_payloads.append(
        {
            "name": name,
            "placement": row["placement"],
            "offset_bytes": actual_offset,
            "size_bytes": size,
            "minimum_size_bytes": minimum,
            "maximum_size_bytes": maximum,
            "sha256": row["sha256"],
        }
    )

boot_contract = board["boot_media_contract"]
boot0_payload = board["emmc_boot0_payload"]
if boot0_payload not in digests:
    raise SystemExit("R3 Mini boot0 載荷缺少固定雜湊")
if digests.get("gpt") != board["gpt_template_sha256"]:
    raise SystemExit("R3 Mini GPT 載荷與範本雜湊不一致")

status.update(
    {
        "candidate_scope": state["scope"],
        "full_rootfs_image_built": state["full_image_built"],
        "internal_candidate_only": True,
        "public_release_authorized": False,
        "hardware_claims_allowed": False,
        "hardware_validation_completed": False,
        "release_gate": {
            "status": "blocked",
            "public_release_authorized": False,
            "hardware_claims_allowed": False,
            "full_image_built": state["full_image_built"],
            "component_validation_only": state["component_validation_only"],
            "blockers": policy["public_release_blockers"],
        },
        "emmc_image_contract": {
            "cold_boot_source": boot_contract["cold_boot_source"],
            "candidate_boot_media": board["candidate_boot_media"],
            "supported_boot_media": board["supported_boot_media"],
            "unsupported_boot_media": board["unsupported_boot_media"],
            "user_area": {
                "target": board["emmc_user_area_target"],
                "contains_gpt": boot_contract["user_area_contains_gpt"],
                "gpt_template_sha256": board["gpt_template_sha256"],
                "image_is_complete_cold_boot_installer": boot_contract[
                    "user_area_image_is_complete_cold_boot_installer"
                ],
            },
            "boot0": {
                "target": board["emmc_boot0_target"],
                "payload": boot0_payload,
                "payload_sha256": digests[boot0_payload],
                "offset_bytes": board["emmc_boot0_offset_bytes"],
                "requires_separate_write": boot_contract[
                    "boot0_payload_requires_separate_write"
                ],
                "force_ro_required": board["emmc_boot0_force_ro_required"],
                "boot_partition_enable": board["emmc_boot_partition_enable"],
                "hardware_validated": boot_contract["boot0_hardware_validated"],
            },
            "automatic_install_authorized": board["automatic_emmc_install_authorized"],
        },
        "verified_payload_boundaries": sorted(
            verified_payloads, key=lambda item: item["name"]
        ),
    }
)
temporary = status_path + ".r3mini"
with open(temporary, "w", encoding="utf-8") as stream:
    json.dump(status, stream, ensure_ascii=False, indent=2)
    stream.write("\n")
os.replace(temporary, status_path)
PY

echo "R3 Mini 載荷身分、邊界、eMMC boot0 與發布阻擋已寫入"
