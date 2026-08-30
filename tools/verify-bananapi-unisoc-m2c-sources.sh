#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
CONTRACT="${CONTRACT:-${REPO_ROOT}/config/validation/bananapi-unisoc-uis7885-m2c-vendor.json}"
SOURCE_ROOT="${SOURCE_ROOT:-/media/pi/SMCI/bpi/unisoc/sync-20260524/source_sync_rls_25c}"
REPORT="${REPORT:-}"

usage() {
	cat <<-EOF
	用法：$0 [--source-root PATH] [--contract PATH] [--report PATH]

	唯讀驗證 Banana Pi M2C 的 Unisoc 來源、解析後 manifest、專案提交、
	本機追蹤差異與必要板級檔案。此工具不編譯元件、不簽署，也不產生 PAC。
	EOF
}

while (($#)); do
	case "$1" in
		--source-root)
			shift
			SOURCE_ROOT="${1:?缺少來源路徑}"
			;;
		--contract)
			shift
			CONTRACT="${1:?缺少契約路徑}"
			;;
		--report)
			shift
			REPORT="${1:?缺少報告路徑}"
			;;
		-h | --help)
			usage
			exit 0
			;;
		*)
			printf '不支援的參數：%s\n' "$1" >&2
			usage >&2
			exit 2
			;;
	esac
	shift
done

for command_name in git python3 sha256sum; do
	command -v "${command_name}" >/dev/null 2>&1 || {
		printf '缺少必要命令：%s\n' "${command_name}" >&2
		exit 1
	}
done

[[ -f "${CONTRACT}" ]] || {
	printf '找不到驗證契約：%s\n' "${CONTRACT}" >&2
	exit 1
}
[[ -d "${SOURCE_ROOT}" ]] || {
	printf '找不到來源目錄：%s\n' "${SOURCE_ROOT}" >&2
	exit 1
}

if [[ -n "${REPORT}" ]]; then
	mkdir -p "$(dirname "${REPORT}")"
fi

python3 - "${CONTRACT}" "${SOURCE_ROOT}" "${REPORT}" <<'PY'
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from pathlib import PurePosixPath
import re
import subprocess
import sys
import xml.etree.ElementTree as ET


contract_path = Path(sys.argv[1]).resolve()
source_root = Path(sys.argv[2]).resolve()
report_path = Path(sys.argv[3]).resolve() if sys.argv[3] else None
contract = json.loads(contract_path.read_text(encoding="utf-8"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def git(path: Path, *arguments: str, binary: bool = False):
    result = subprocess.run(
        [
            "git",
            "-c",
            "core.compression=0",
            "-c",
            "color.ui=false",
            "-c",
            "core.quotePath=false",
            "-C",
            str(path),
            *arguments,
        ],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=not binary,
    )
    if result.returncode != 0:
        error = result.stderr.decode(errors="replace") if binary else result.stderr
        raise RuntimeError(f"Git 命令失敗：{path}: {error.strip()}")
    return result.stdout


errors: list[str] = []
source = contract["source"]
manifest_path = (source_root / source["resolved_manifest"]).resolve()

untracked_contract = source.get("untracked_inputs", {})
if untracked_contract.get("policy") != "deny-unless-allowlisted":
    errors.append("未追蹤輸入政策必須是 deny-unless-allowlisted")

canonical_allowlist_fields = {"path", "sha256", "purpose", "license_status"}
declared_allowlist_fields = set(
    untracked_contract.get("allowlist_required_fields", [])
)
if declared_allowlist_fields != canonical_allowlist_fields:
    errors.append("未追蹤輸入允許清單欄位契約不完整")

allowlisted_untracked: dict[str, dict[str, str]] = {}
raw_allowlist = untracked_contract.get("allowlist", [])
if not isinstance(raw_allowlist, list):
    errors.append("未追蹤輸入允許清單必須是陣列")
    raw_allowlist = []
for item in raw_allowlist:
    if not isinstance(item, dict) or not canonical_allowlist_fields.issubset(item):
        errors.append("未追蹤輸入允許清單項目缺少必要欄位")
        continue
    relative = item["path"]
    if not all(isinstance(item[field], str) for field in canonical_allowlist_fields):
        errors.append(f"未追蹤輸入允許清單欄位必須是字串：{relative}")
        continue
    relative_path = PurePosixPath(relative)
    if (
        not relative
        or relative_path.is_absolute()
        or ".." in relative_path.parts
        or relative in allowlisted_untracked
    ):
        errors.append(f"未追蹤輸入允許路徑無效或重複：{relative}")
        continue
    if not re.fullmatch(r"[0-9a-f]{64}", item["sha256"]):
        errors.append(f"未追蹤輸入 SHA-256 格式錯誤：{relative}")
        continue
    if not item["purpose"].strip():
        errors.append(f"未追蹤輸入缺少用途：{relative}")
        continue
    if item["license_status"] not in {
        "local-use-authorized",
        "redistribution-authorized",
    }:
        errors.append(f"未追蹤輸入授權狀態不允許：{relative}")
        continue
    allowlisted_untracked[relative] = item

if not manifest_path.is_file():
    errors.append(f"找不到解析後 manifest：{manifest_path}")
elif sha256_file(manifest_path) != source["resolved_manifest_sha256"]:
    errors.append("解析後 manifest 的 SHA-256 不符")

projects: dict[str, str] = {}
if not errors:
    manifest = ET.parse(manifest_path).getroot()
    for element in manifest.findall("project"):
        project_path = element.get("path") or element.get("name")
        revision = element.get("revision", "")
        project_posix = PurePosixPath(project_path or "")
        if (
            not project_path
            or project_posix.is_absolute()
            or ".." in project_posix.parts
            or not re.fullmatch(r"[0-9a-f]{40}", revision)
        ):
            errors.append(f"manifest 專案缺少固定提交：{project_path or '未知'}")
            continue
        projects[project_path] = revision

if len(projects) != source["project_count"]:
    errors.append(
        f"manifest 專案數不符：預期 {source['project_count']}，實際 {len(projects)}"
    )

empty_diff = hashlib.sha256(b"").hexdigest()
actual_diffs: dict[str, str] = {}
actual_untracked: dict[str, Path] = {}
untracked_projects: set[str] = set()
for relative_path, revision in sorted(projects.items()):
    project_root = source_root / relative_path
    if not (project_root / ".git").exists():
        errors.append(f"缺少 Git 專案：{relative_path}")
        continue
    try:
        actual_revision = git(project_root, "rev-parse", "HEAD").strip()
        if actual_revision != revision:
            errors.append(
                f"專案提交不符：{relative_path}，預期 {revision}，實際 {actual_revision}"
            )
        staged = git(project_root, "diff", "--cached", "--name-only").strip()
        if staged:
            errors.append(f"專案含有 staged 差異：{relative_path}")
        diff = git(project_root, "diff", "--binary", "--no-ext-diff", binary=True)
        diff_hash = hashlib.sha256(diff).hexdigest()
        if diff_hash != empty_diff:
            actual_diffs[relative_path] = diff_hash
        untracked_output = git(
            project_root,
            "ls-files",
            "--others",
            "--exclude-standard",
            "-z",
            binary=True,
        )
        for encoded_path in untracked_output.split(b"\0"):
            if not encoded_path:
                continue
            local_path = os.fsdecode(encoded_path)
            source_relative = f"{relative_path}/{local_path}"
            actual_untracked[source_relative] = project_root / local_path
            untracked_projects.add(relative_path)
    except RuntimeError as error:
        errors.append(str(error))

expected_diffs = source["expected_local_diffs"]
for relative_path in sorted(set(actual_diffs) | set(expected_diffs)):
    expected = expected_diffs.get(relative_path)
    actual = actual_diffs.get(relative_path)
    if expected != actual:
        errors.append(
            f"本機追蹤差異不符：{relative_path}，預期 {expected or '無'}，實際 {actual or '無'}"
        )

unclassified_untracked = sorted(set(actual_untracked) - set(allowlisted_untracked))
missing_allowlisted = sorted(set(allowlisted_untracked) - set(actual_untracked))
if unclassified_untracked:
    errors.append(
        f"共有 {len(unclassified_untracked)} 個未分類未追蹤輸入，來源守門拒絕通過"
    )
for relative_path in missing_allowlisted:
    errors.append(f"允許清單中的未追蹤輸入不存在：{relative_path}")
for relative_path in sorted(set(actual_untracked) & set(allowlisted_untracked)):
    path = actual_untracked[relative_path]
    if path.is_symlink() or not path.is_file():
        errors.append(f"允許清單中的未追蹤輸入不是一般檔案：{relative_path}")
    elif sha256_file(path) != allowlisted_untracked[relative_path]["sha256"]:
        errors.append(f"允許清單中的未追蹤輸入 SHA-256 不符：{relative_path}")

known_unclassified = untracked_contract.get("known_unclassified", {})
if known_unclassified.get("blocking") is not False:
    errors.append("契約仍標記未追蹤輸入分類為阻擋狀態")

for item in contract["required_files"]:
    path = source_root / item["path"]
    if not path.is_file():
        errors.append(f"缺少必要板級檔案：{item['path']}")
    elif sha256_file(path) != item["sha256"]:
        errors.append(f"必要板級檔案 SHA-256 不符：{item['path']}")

for item in contract["external_patch_evidence"]:
    path = (source_root / item["path"]).resolve()
    if not path.is_file():
        errors.append(f"缺少外部修補證據：{item['path']}")
    elif sha256_file(path) != item["sha256"]:
        errors.append(f"外部修補證據 SHA-256 不符：{item['path']}")

content_requirements = {
    "layers/meta-unisoc/conf/machine/uis7885-2h10.conf": [
        'KERNEL_BOARD = "uis7885-2h10"',
        'UBOOT_BOARD = "uis7885_2h10"',
        'SUPPORT_EMMC_UFS_SDBOOT = "yes"',
    ],
    "prebuilts/pac_config/uis7885-2h10-uboot22.ini": [
        "SPLLoaderSDBOOT=1@./out/target/product/uis7885-2h10/u-boot-spl-16k-sign.bin",
        "BOOT=1@./out/target/product/uis7885-2h10/boot-sign.img",
        "DTBO=1@./out/target/product/uis7885-2h10/dtbo-sign.img",
    ],
    "layers/meta-unisoc/recipes-bsp/u-boot/u-boot22.bb": [
        "inherit sign_unisoc_binary",
        'UNISOC_SIGN_ENABLE ?= "no"',
    ],
    "layers/meta-unisoc/recipes-bsp/chipram/chipram.bb": [
        "inherit sign_unisoc_binary deploy",
        'UNISOC_SIGN_ENABLE ?= "yes"',
    ],
}
for relative_path, needles in content_requirements.items():
    path = source_root / relative_path
    if not path.is_file():
        errors.append(f"缺少流程檔案：{relative_path}")
        continue
    text = path.read_text(encoding="utf-8", errors="replace")
    for needle in needles:
        if needle not in text:
            errors.append(f"流程契約缺少必要內容：{relative_path}: {needle}")

manifest_repo = source_root / ".repo" / "manifests"
if manifest_repo.is_dir():
    try:
        manifest_revision = git(manifest_repo, "rev-parse", "HEAD").strip()
        if manifest_revision != source["manifest_commit"]:
            errors.append("manifest 倉提交不符")
    except RuntimeError as error:
        errors.append(str(error))
else:
    errors.append("缺少 .repo/manifests")

lines = [
    "Banana Pi M2C Unisoc 來源候選唯讀驗證",
    "========================================",
    f"來源目錄：{source_root}",
    f"基線：{source['baseline']}",
    f"manifest 提交：{source['manifest_commit']}",
    f"解析後 manifest SHA-256：{source['resolved_manifest_sha256']}",
    f"固定來源專案數：{len(projects)}",
    f"固定本機追蹤差異數：{len(actual_diffs)}",
    f"含未追蹤檔專案數：{len(untracked_projects)}",
    f"未追蹤檔總數：{len(actual_untracked)}",
    f"允許清單檔案數：{len(allowlisted_untracked)}",
    f"未分類未追蹤檔數：{len(unclassified_untracked)}",
    "候選目前證據等級：L0",
    "候選範圍：本機來源快照稽核",
    "公開發布允許：否",
    "硬體功能聲明允許：否",
]
lines.extend(f"未分類未追蹤輸入：{path}" for path in unclassified_untracked)
if errors:
    lines.append("結果：失敗")
    lines.extend(f"錯誤：{error}" for error in errors)
else:
    lines.extend(
        [
            "結果：通過",
            "結論：來源提交、本機追蹤差異及未追蹤輸入符合 L0 本機快照契約。",
            "限制：此結果不代表來源可重放、元件可公開再散布、映像已建立、PAC 可重現、SD 已開機或一般 Armbian 支援。",
        ]
    )

report = "\n".join(lines) + "\n"
if report_path:
    report_path.write_text(report, encoding="utf-8")
else:
    sys.stdout.write(report)

if errors:
    raise SystemExit(1)
PY
