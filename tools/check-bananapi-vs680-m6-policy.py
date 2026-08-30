#!/usr/bin/env python3
"""檢查 Banana Pi M6 固定來源、啟動鏈與證據狀態。"""

from __future__ import annotations

import argparse
import csv
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import lzma
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config/validation/bananapi-vs680-m6-legacy.json"
BOARD = ROOT / "config/boards/bananapim6.wip"
STATUS = ROOT / "config/bananapi-optimization-status.json"
FAMILY = ROOT / "config/sources/families/vs680.conf"

LINUX_REVISION = "3229415e99a06edc972948c0a856cbcf7de7ce55"
UBOOT_REVISION = "ccca1c75bb6d06470b8a3f6104068b43763ee468"
FIRMWARE_REVISION = "f50a2a21bcdb77a562b3976930c5c6b521a1df08"
FINAL_KERNEL_CONFIG = "b67480db7854ea797a1813102b2ef1c7a1312c9291797912612368821b058786"
FINAL_UBOOT_CONFIG = "f31af0f1449901eb3834fd17e9c8c69034bd50b126a29108168683ba6b38c1f6"
COMPONENT_DTB = "52c58e8a1413fd644b812480215350410659371083afa9930684df5752625413"
TZK_SHA256 = "175e9b9313dffb70a97852ae21d855d3472916cc2af28f678ebcddc44828e411"
UBOOT_SHA256 = "4d8158b3ed44de9384fabb009a0639cbe2c83e964a32724b5c87ce9911f72bda"
VALIDATION_RELATIVE = "config/validation/bananapi-vs680-m6-legacy.json"
OUTPUT_DIR = (
    ROOT
    / "output/images/2026.08"
    / "bananapi-vs680-m6-trixie-legacy-cli"
)
MATRIX = OUTPUT_DIR / "CANDIDATES.tsv"
COMPLETION_STATUS = OUTPUT_DIR / "COMPLETION_STATUS.json"
VERIFICATION_STATUS = OUTPUT_DIR / "VERIFICATION_STATUS.json"
VERIFICATION_MANIFEST = OUTPUT_DIR / "VERIFICATION.tsv"
UBOOT_PAYLOAD_EVIDENCE = OUTPUT_DIR / "UBOOT_PAYLOAD_EVIDENCE.tsv"
FINAL_CONFIG_EVIDENCE = OUTPUT_DIR / "FINAL_CONFIG_EVIDENCE.tsv"
MATERIAL_EVIDENCE = OUTPUT_DIR / "M6_MATERIAL_EVIDENCE.json"
MATERIAL_STATUS = OUTPUT_DIR / "M6_MATERIAL_STATUS.json"
CALIBRATION_EVIDENCE = OUTPUT_DIR / "M6_CALIBRATION.json"
METADATA = OUTPUT_DIR / "bananapim6/artifact.metadata.txt"
TZK_SOURCE = ROOT / "packages/blobs/vs680/bpi-m6-tzk-4MB.bin"
EXPECTED_SOURCE_DATE_EPOCH = 1717001894
CONTRACT_PROJECTION_EXCLUDED_TOP_LEVEL = {
    "candidate_level",
    "candidate_scope",
    "component_build_completed",
    "component_build_evidence",
    "source_contract_projection_sha256",
    "current_evidence_level",
    "full_image_built",
    "full_rootfs_image_built",
    "image_build_evidence",
    "rootfs_image_built",
}
CONTRACT_PROJECTION_EXCLUDED_BOARD_FIELDS = {
    "dtb_sha256",
    "dtb_sha256_evidence_scope",
    "image_dtb_sha256",
}


def fail(message: str) -> None:
    raise SystemExit(f"BPI-M6 政策守門失敗：{message}")


def require(condition: bool, message: str) -> None:
    if not condition:
        fail(message)


def valid_sha256(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def valid_commit(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{40}", value) is not None


def valid_relative_artifact_path(value: object, suffix: str) -> bool:
    return (
        isinstance(value, str)
        and value.endswith(suffix)
        and not value.startswith("/")
        and ".." not in Path(value).parts
    )


def contract_projection(policy: dict[str, object]) -> dict[str, object]:
    """建立排除候選狀態與物質證據的穩定來源契約投影。"""
    projection = deepcopy(policy)
    for key in CONTRACT_PROJECTION_EXCLUDED_TOP_LEVEL:
        projection.pop(key, None)
    boards = projection.get("boards", {})
    if isinstance(boards, dict):
        for board in boards.values():
            if isinstance(board, dict):
                for key in CONTRACT_PROJECTION_EXCLUDED_BOARD_FIELDS:
                    board.pop(key, None)
    return projection


def contract_projection_sha256(policy: dict[str, object]) -> str:
    encoded = json.dumps(
        contract_projection(policy),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def validate_contract_projection(
    policy: dict[str, object], require_evidence_binding: bool
) -> str:
    actual = contract_projection_sha256(policy)
    declared = policy.get("source_contract_projection_sha256")
    if declared is not None:
        require(valid_sha256(declared), "來源契約投影雜湊格式不符")
        require(declared == actual, "現行來源契約與固定投影雜湊不符")
    if require_evidence_binding:
        evidence = policy.get("image_build_evidence", {})
        require(isinstance(evidence, dict), "L2 缺少完整映像證據")
        require(
            evidence.get("source_contract_projection_sha256") == actual,
            "L2 證據未綁定現行來源契約投影",
        )
    return actual


def load_json(path: Path, description: str) -> dict[str, object]:
    require(path.is_file(), f"缺少{description}：{display_path(path)}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        fail(f"{description}不是有效 JSON：{error}")
    require(isinstance(value, dict), f"{description}格式不符")
    return value


def display_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def load_tsv(
    path: Path, expected_header: list[str], description: str
) -> list[dict[str, str]]:
    require(path.is_file(), f"缺少{description}：{display_path(path)}")
    with path.open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream, delimiter="\t")
        require(reader.fieldnames == expected_header, f"{description}欄位不符")
        rows = list(reader)
    require(rows, f"{description}不得為空")
    for row in rows:
        require(
            None not in row and all(value is not None for value in row.values()),
            f"{description}列格式不符",
        )
    return rows


def sha256_stream(stream) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    for block in iter(lambda: stream.read(1024 * 1024), b""):
        size += len(block)
        digest.update(block)
    return size, digest.hexdigest()


def sha256_range(path: Path, offset: int, size: int) -> str:
    require(offset >= 0 and size >= 0, "雜湊範圍不可為負數")
    digest = hashlib.sha256()
    remaining = size
    with path.open("rb") as stream:
        stream.seek(offset)
        while remaining:
            block = stream.read(min(1024 * 1024, remaining))
            require(block != b"", f"讀取範圍超出檔案：{display_path(path)}")
            remaining -= len(block)
            digest.update(block)
    return digest.hexdigest()


def resolve_matrix_artifact(relative: object, suffix: str, description: str) -> Path:
    require(valid_relative_artifact_path(relative, suffix), f"{description}路徑不合法")
    require(isinstance(relative, str), f"{description}路徑不合法")
    segments = relative.split("/")
    require(
        len(segments) == 2 and segments[0] == "bananapim6",
        f"{description}不在固定板卡產物目錄",
    )
    path = (OUTPUT_DIR / relative).resolve()
    expected_parent = (OUTPUT_DIR / "bananapim6").resolve()
    require(path.parent == expected_parent, f"{description}路徑逸出固定輸出目錄")
    require(path.is_file(), f"缺少{description}：{relative}")
    return path


def validate_xz_stream_matches_image(
    image: Path, archive: Path, evidence: dict[str, object]
) -> None:
    require(shutil.which("xz") is not None, "缺少嚴格 XZ 結構驗證命令：xz")
    structure = subprocess.run(
        ["xz", "-t", "--", str(archive)],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    require(structure.returncode == 0, "L2 XZ 結構、結尾或校驗碼不符")
    try:
        with lzma.open(archive, "rb") as stream:
            size, digest = sha256_stream(stream)
    except (EOFError, lzma.LZMAError) as error:
        fail(f"L2 XZ 串流損毀：{error}")
    image_evidence = evidence["image"]
    require(isinstance(image_evidence, dict), "L2 IMG 證據格式不符")
    require(size == image.stat().st_size, "L2 XZ 解壓大小與 IMG 不同")
    require(digest == image_evidence.get("sha256"), "L2 XZ 解壓內容與 IMG 不同")


def git_output(*arguments: str) -> bytes:
    result = subprocess.run(
        ["git", "-C", str(ROOT), *arguments],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    require(result.returncode == 0, f"Git 證據不存在：{' '.join(arguments)}")
    return result.stdout


def validate_l2_git_evidence(
    policy: dict[str, object], evidence: dict[str, object]
) -> None:
    source_commit = evidence["source_commit"]
    git_output("cat-file", "-e", f"{source_commit}^{{commit}}")
    ancestry = subprocess.run(
        ["git", "-C", str(ROOT), "merge-base", "--is-ancestor", source_commit, "HEAD"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    require(ancestry.returncode == 0, "L2 來源提交不是目前分支的祖先")
    actual_tree = git_output("rev-parse", f"{source_commit}^{{tree}}").decode().strip()
    require(evidence.get("source_tree") == actual_tree, "L2 來源 tree 與提交不一致")
    validation_blob = git_output("show", f"{source_commit}:{VALIDATION_RELATIVE}")
    expected_validation = hashlib.sha256(validation_blob).hexdigest()
    require(
        evidence.get("build_validation_config_sha256") == expected_validation,
        "L2 建置 validation 雜湊與來源提交不一致",
    )
    require(
        evidence.get("verification_config_sha256") == expected_validation,
        "L2 驗證 validation 雜湊與來源提交不一致",
    )
    try:
        source_policy = json.loads(validation_blob.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        fail(f"L2 來源提交的 validation 無法解析：{error}")
    projection = contract_projection_sha256(policy)
    require(
        contract_projection_sha256(source_policy) == projection,
        "L2 來源提交與現行來源契約投影不一致",
    )
    require(
        evidence.get("source_contract_projection_sha256") == projection,
        "L2 證據未綁定來源契約投影",
    )


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def assignments(values: object, field: str) -> dict[str, str]:
    require(isinstance(values, list), f"{field} 必須是清單")
    parsed: dict[str, str] = {}
    for value in values:
        require(isinstance(value, str) and value.count("=") == 1, f"{field} 格式不符")
        name, expected = value.split("=", 1)
        require(name and expected and name not in parsed, f"{field} 含空值或重複名稱")
        parsed[name] = expected
    return parsed


def validate_candidate_state(
    data: dict[str, object],
    status: dict[str, object],
    require_material_binding: bool = True,
) -> None:
    level = data.get("candidate_level")
    require(level in {"L1 元件候選", "L2 內部軟體候選"}, "候選層級只允許 L1 或內部 L2")
    expected = {
        "L1 元件候選": ("internal-component-only", "L1"),
        "L2 內部軟體候選": ("internal-l2", "L2"),
    }[level]
    require(data.get("candidate_scope") == expected[0], "候選層級與範圍不成對")
    require(data.get("current_evidence_level") == expected[1], "候選層級與證據等級不成對")
    central_level = status["evidence"]["bananapim6"]["level"]
    if level == "L2 內部軟體候選" and not require_material_binding:
        require(
            central_level in {"L1", "L2"},
            "L2 正式重建期間的中央證據等級只能是 L1 或 L2",
        )
    else:
        require(central_level == expected[1], "中央證據等級與契約不一致")
    require(data.get("component_build_completed") is True, "固定來源元件證據必須保留")

    for key in (
        "candidate_public_release_approved",
        "public_release_allowed",
        "hardware_validation_complete",
        "hardware_claims_allowed",
        "firmware_redistribution_license_verified",
    ):
        require(data.get(key) is False, f"{key} 必須維持 false")
    require(
        data["license_policy"]["opaque_payload_redistribution_verified"] is False,
        "不透明載荷不得宣稱授權已閉合",
    )

    board = data["boards"]["bananapim6"]
    require(board.get("component_dtb_sha256") == COMPONENT_DTB, "元件 DTB 雜湊不符")
    if level == "L1 元件候選":
        for key in ("rootfs_image_built", "full_image_built", "full_rootfs_image_built"):
            require(data.get(key) is False, f"L1 的 {key} 必須為 false")
        require("image_build_evidence" not in data, "L1 不得夾帶完整映像證據")
        require(board.get("image_dtb_sha256") is None, "L1 不得宣稱映像 DTB 雜湊")
        require("dtb_sha256" not in board, "L1 不得把元件 DTB 冒充映像證據")
        require(
            board.get("dtb_sha256_evidence_scope") == "component-only-l1",
            "L1 DTB 證據範圍不符",
        )
        return

    if not require_material_binding:
        return

    for key in ("rootfs_image_built", "full_image_built", "full_rootfs_image_built"):
        require(data.get(key) is True, f"L2 的 {key} 必須為 true")
    evidence = data.get("image_build_evidence")
    require(isinstance(evidence, dict), "L2 缺少完整映像證據")
    require(evidence.get("status") == "complete", "L2 映像證據尚未完成")
    require(evidence.get("evidence_level") == "L2", "映像證據不是 L2")
    require(evidence.get("read_only_content_verified") is True, "L2 缺少唯讀內容驗證")
    require(evidence.get("hardware_tested") is False, "內部 L2 不得冒充實機驗證")
    require(valid_commit(evidence.get("source_commit")), "L2 來源提交格式不符")
    require(valid_commit(evidence.get("verifier_commit")), "L2 驗證器提交格式不符")
    require(
        evidence["source_commit"] == evidence["verifier_commit"],
        "L2 來源與驗證器提交不一致",
    )
    require(valid_commit(evidence.get("source_tree")), "L2 來源 tree 格式不符")
    require(evidence.get("source_date_epoch") == 1717001894, "L2 來源時間戳不符")
    require(evidence.get("xz_stream_verified") is True, "L2 缺少 XZ 串流同一性驗證")
    for key in (
        "build_validation_config_sha256",
        "verification_config_sha256",
        "candidate_matrix_sha256",
        "completion_status_sha256",
        "verification_manifest_sha256",
        "uboot_payload_manifest_sha256",
        "final_config_manifest_sha256",
    ):
        require(valid_sha256(evidence.get(key)), f"L2 {key} 格式不符")
    require(
        evidence["build_validation_config_sha256"]
        == evidence["verification_config_sha256"],
        "L2 建置與驗證契約雜湊不一致",
    )
    validate_l2_git_evidence(data, evidence)
    for name in ("image", "archive"):
        artifact = evidence.get(name, {})
        require(isinstance(artifact, dict), f"L2 {name} 證據格式不符")
        require(isinstance(artifact.get("size"), int) and artifact["size"] > 0, f"L2 {name} 大小無效")
        require(valid_sha256(artifact.get("sha256")), f"L2 {name} 雜湊格式不符")
    require(valid_relative_artifact_path(evidence["image"].get("path"), ".img"), "L2 IMG 路徑不合法")
    require(valid_relative_artifact_path(evidence["archive"].get("path"), ".img.xz"), "L2 XZ 路徑不合法")
    image_dtb = evidence.get("linux_dtb", {}).get("sha256")
    require(valid_sha256(image_dtb), "L2 缺少映像 DTB 雜湊")
    require(board.get("image_dtb_sha256") == image_dtb, "映像 DTB 欄位與證據不一致")
    require(board.get("dtb_sha256") == image_dtb, "映像 DTB 相容欄位與證據不一致")
    require(board.get("dtb_sha256_evidence_scope") == "full-image-l2", "L2 DTB 證據範圍不符")


def load_candidate_row() -> dict[str, str]:
    rows = load_tsv(
        MATRIX,
        [
            "board",
            "release",
            "profile",
            "raw_size",
            "raw_sha256",
            "xz_size",
            "xz_sha256",
            "img_path",
            "xz_path",
            "source_commit",
            "uboot_tag",
        ],
        "候選矩陣",
    )
    require(
        len(rows) == 1
        and rows[0]["board"] == "bananapim6"
        and rows[0]["release"] == "trixie"
        and rows[0]["profile"] == "cli",
        "候選矩陣必須只有 BPI-M6 trixie CLI 一筆",
    )
    return rows[0]


def validate_verification_manifest(evidence_level: str = "L2") -> str:
    require(evidence_level in {"L1", "L2"}, "共用驗證清單證據層級無效")
    rows = load_tsv(
        VERIFICATION_MANIFEST,
        ["board", "identity", "read_only_content", "evidence_level"],
        "共用驗證清單",
    )
    require(
        rows
        == [
            {
                "board": "bananapim6",
                "identity": "pass",
                "read_only_content": "pass",
                "evidence_level": evidence_level,
            }
        ],
        "共用驗證清單結果不符",
    )
    return file_sha256(VERIFICATION_MANIFEST)


def validate_dual_partition_contract(
    policy: dict[str, object], image: Path
) -> dict[str, object]:
    require(shutil.which("sfdisk") is not None, "缺少 MBR 分割區驗證命令：sfdisk")
    try:
        table = json.loads(
            subprocess.check_output(["sfdisk", "--json", str(image)], text=True)
        )["partitiontable"]
    except (subprocess.CalledProcessError, KeyError, json.JSONDecodeError) as error:
        fail(f"無法解析 M6 映像分割表：{error}")
    board = policy["boards"]["bananapim6"]
    require(table.get("label") in {"dos", "msdos"}, "M6 映像分割表不是 MBR")
    require(table.get("unit") == "sectors", "M6 分割表單位不是 sectors")
    sector_size = table.get("sectorsize", board.get("logical_sector_size"))
    require(sector_size == board["logical_sector_size"], "M6 邏輯 sector 大小不符")
    partitions = table.get("partitions", [])
    require(isinstance(partitions, list) and len(partitions) == 2, "M6 必須有兩個分割區")
    expected = (
        (204800, 524288, "ea"),
        (729088, None, "83"),
    )
    summary: list[dict[str, object]] = []
    for index, (partition, specification) in enumerate(zip(partitions, expected), 1):
        require(isinstance(partition, dict), f"M6 第 {index} 分割區格式不符")
        start, size, partition_type = specification
        actual_type = str(partition.get("type", "")).lower().removeprefix("0x")
        require(partition.get("start") == start, f"M6 第 {index} 分割區起點不符")
        require(
            isinstance(partition.get("size"), int) and partition["size"] > 0,
            f"M6 第 {index} 分割區大小無效",
        )
        if size is not None:
            require(partition["size"] == size, f"M6 第 {index} 分割區大小不符")
        require(actual_type == partition_type, f"M6 第 {index} 分割區類型不符")
        summary.append(
            {
                "number": index,
                "start": partition["start"],
                "size": partition["size"],
                "type": actual_type,
            }
        )
    return {"label": "msdos", "logical_sector_size": sector_size, "partitions": summary}


def validate_payload_overlap_manifest(
    policy: dict[str, object], image: Path
) -> dict[str, object]:
    rows = load_tsv(
        UBOOT_PAYLOAD_EVIDENCE,
        ["board", "payload", "placement", "offset", "size", "sha256"],
        "U-Boot 載荷清單",
    )
    board = policy["boards"]["bananapim6"]
    locations: dict[str, int] = {}
    for item in board["uboot_payloads"]:
        require(isinstance(item, str) and item.count("@") == 1, "payload 位移格式不符")
        name, offset = item.rsplit("@", 1)
        require(offset.isdigit() and name not in locations, "payload 位移含重複或無效值")
        locations[name] = int(offset)
    sizes = {name: int(value) for name, value in assignments(board["uboot_payload_sizes"], "payload 大小").items()}
    hashes = assignments(board["uboot_payload_sha256"], "payload 雜湊")
    require(set(locations) == set(sizes) == set(hashes), "payload 契約集合不一致")
    require(len(rows) == 2, "M6 重疊載荷清單必須有兩筆")
    seen: set[str] = set()
    for row in rows:
        name = row["payload"]
        require(name in locations and name not in seen, f"載荷清單含未知或重複項目：{name}")
        seen.add(name)
        require(row["board"] == "bananapim6", "載荷清單板卡不符")
        require(row["placement"] == "image-controlled-overlap", "載荷未標示受控重疊")
        require(row["offset"] == str(locations[name]), f"載荷位移不符：{name}")
        require(row["size"] == str(sizes[name]), f"載荷大小不符：{name}")
        require(row["sha256"] == hashes[name], f"載荷雜湊不符：{name}")

    overlap = board["payload_overlap_policy"]
    earlier = overlap["earlier_payload"]
    later = overlap["later_payload"]
    earlier_offset = locations[earlier]
    later_offset = locations[later]
    earlier_end = earlier_offset + sizes[earlier]
    later_end = later_offset + sizes[later]
    require(board["payload_write_order"] == [earlier, later], "payload 寫入順序不符")
    require(later_offset == overlap["overlap_starts_at_image_offset"], "payload 重疊起點不符")
    require(earlier_offset < later_offset < later_end < earlier_end, "payload 重疊範圍不符")
    require(TZK_SOURCE.is_file(), "缺少固定 TZK 來源檔")
    require(TZK_SOURCE.stat().st_size == sizes[earlier], "固定 TZK 大小不符")
    require(file_sha256(TZK_SOURCE) == hashes[earlier], "固定 TZK 雜湊不符")

    prefix_size = later_offset - earlier_offset
    require(
        sha256_range(image, earlier_offset, prefix_size)
        == sha256_range(TZK_SOURCE, 0, prefix_size),
        "IMG 中 TZK 前段與固定來源不同",
    )
    require(
        sha256_range(image, later_offset, sizes[later]) == hashes[later],
        "IMG 中完整 U-Boot 載荷雜湊不符",
    )
    tail_size = earlier_end - later_end
    source_tail_offset = later_end - earlier_offset
    require(
        sha256_range(image, later_end, tail_size)
        == sha256_range(TZK_SOURCE, source_tail_offset, tail_size),
        "IMG 中 TZK 尾段與固定來源不同",
    )
    return {
        "write_order": [earlier, later],
        "overlap_start": later_offset,
        "prefix_size": prefix_size,
        "later_payload_size": sizes[later],
        "tail_size": tail_size,
        "manifest_sha256": file_sha256(UBOOT_PAYLOAD_EVIDENCE),
    }


def validate_final_config_manifest(policy: dict[str, object]) -> dict[str, str]:
    rows = load_tsv(
        FINAL_CONFIG_EVIDENCE,
        ["board", "component", "path", "sha256"],
        "最終設定清單",
    )
    board = policy["boards"]["bananapim6"]
    expected_hashes = {
        "kernel": board["final_kernel_config_sha256"],
        "uboot": board["final_uboot_config_sha256"],
    }
    require(len(rows) == 2, "最終設定清單必須有核心與 U-Boot 兩筆")
    paths: dict[str, str] = {}
    for row in rows:
        component = row["component"]
        require(component in expected_hashes and component not in paths, "最終設定清單含未知或重複項目")
        require(row["board"] == "bananapim6", "最終設定清單板卡不符")
        require(
            row["path"] and not row["path"].startswith("/") and ".." not in Path(row["path"]).parts,
            f"最終設定路徑不合法：{component}",
        )
        require(row["sha256"] == expected_hashes[component], f"最終設定雜湊不符：{component}")
        paths[component] = row["path"]
    return paths


def _blkid_fields(device: str) -> dict[str, str]:
    output = subprocess.check_output(
        ["sudo", "-n", "blkid", "-p", "-o", "export", device], text=True
    )
    return dict(line.split("=", 1) for line in output.splitlines() if "=" in line)


def inspect_read_only_image(
    policy: dict[str, object], image: Path, final_config_paths: dict[str, str]
) -> dict[str, object]:
    for command in ("blkid", "losetup", "lsblk", "mount", "sudo", "umount"):
        require(shutil.which(command) is not None, f"缺少唯讀映像驗證命令：{command}")
    temporary_root = ROOT / ".tmp"
    temporary_root.mkdir(exist_ok=True)
    loop_device = ""
    root_mounted = False
    boot_mounted = False
    with tempfile.TemporaryDirectory(prefix="m6-policy.", dir=temporary_root) as directory:
        mount_dir = Path(directory)
        try:
            loop_device = subprocess.check_output(
                [
                    "sudo",
                    "-n",
                    "losetup",
                    "--find",
                    "--show",
                    "--partscan",
                    "--read-only",
                    str(image),
                ],
                cwd=ROOT,
                text=True,
            ).strip()
            require(loop_device.startswith("/dev/loop"), "無法建立唯讀 loop 裝置")
            require(
                subprocess.check_output(["lsblk", "-dnro", "RO", loop_device], text=True).strip() == "1",
                "映像 loop 裝置不是唯讀",
            )
            rows = subprocess.check_output(
                ["lsblk", "-nrpo", "NAME,TYPE", loop_device], text=True
            ).splitlines()
            partitions = [
                fields[0]
                for fields in (row.split() for row in rows)
                if len(fields) == 2 and fields[1] == "part"
            ]
            require(len(partitions) == 2, "唯讀 loop 未呈現兩個 M6 分割區")
            boot_partition, root_partition = partitions
            board = policy["boards"]["bananapim6"]
            boot_fields = _blkid_fields(boot_partition)
            root_fields = _blkid_fields(root_partition)
            require(boot_fields.get("TYPE") == board["boot_partition_filesystem_type"], "boot 檔案系統類型不符")
            require(boot_fields.get("LABEL") == board["boot_partition_label"], "boot 分割區標籤不符")
            require(root_fields.get("TYPE") == board["root_partition_filesystem_type"], "root 檔案系統類型不符")
            require(root_fields.get("LABEL") == board["root_partition_label"], "root 分割區標籤不符")
            subprocess.run(
                ["sudo", "-n", "mount", "-o", "ro,noload,nosuid,nodev,noexec", root_partition, str(mount_dir)],
                check=True,
                cwd=ROOT,
            )
            root_mounted = True
            require((mount_dir / "boot").is_dir(), "root 映像缺少 /boot 掛載點")
            subprocess.run(
                ["sudo", "-n", "mount", "-o", "ro,nosuid,nodev,noexec", boot_partition, str(mount_dir / "boot")],
                check=True,
                cwd=ROOT,
            )
            boot_mounted = True
            resolved_mount = mount_dir.resolve()
            config_summary: dict[str, dict[str, str]] = {}
            for component, relative in final_config_paths.items():
                path = (mount_dir / relative).resolve()
                require(path.is_relative_to(resolved_mount), f"最終設定路徑逸出映像：{component}")
                require(path.is_file(), f"映像缺少最終設定：{relative}")
                expected = board[f"final_{component}_config_sha256"]
                require(file_sha256(path) == expected, f"映像最終設定雜湊不符：{component}")
                config_summary[component] = {"path": relative, "sha256": expected}

            dtb_relative = board["dtb"]
            candidates = (
                mount_dir / "boot/dtb" / dtb_relative,
                mount_dir / "boot/dtb" / Path(dtb_relative).name,
            )
            dtb_matches = [path for path in candidates if path.is_file() and path.stat().st_size > 0]
            require(dtb_matches, f"映像缺少 DTB：{dtb_relative}")
            dtb_digest = file_sha256(dtb_matches[0])
            expected_dtb = board.get("image_dtb_sha256")
            if expected_dtb is not None:
                require(dtb_digest == expected_dtb, "映像 DTB 與 L2 契約雜湊不符")
            return {
                "boot": {"filesystem": boot_fields["TYPE"], "label": boot_fields["LABEL"], "read_only": True},
                "root": {"filesystem": root_fields["TYPE"], "label": root_fields["LABEL"], "read_only": True},
                "linux_dtb": {"path": dtb_relative, "sha256": dtb_digest},
                "final_configs": config_summary,
            }
        except subprocess.CalledProcessError as error:
            fail(f"唯讀映像檢查命令執行失敗：{error}")
        finally:
            if boot_mounted:
                result = subprocess.run(
                    ["sudo", "-n", "umount", str(mount_dir / "boot")],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                require(result.returncode == 0, "無法卸載唯讀 boot 分割區")
            if root_mounted:
                result = subprocess.run(
                    ["sudo", "-n", "umount", str(mount_dir)],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                require(result.returncode == 0, "無法卸載唯讀 root 分割區")
            if loop_device:
                result = subprocess.run(
                    ["sudo", "-n", "losetup", "--detach", loop_device],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                require(result.returncode == 0, "無法卸除唯讀 loop 裝置")


def load_metadata(path: Path) -> dict[str, str]:
    require(path.is_file(), f"缺少產物中繼資料：{display_path(path)}")
    result: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        require("=" in line, "產物中繼資料含無效列")
        key, value = line.split("=", 1)
        require(key not in result, f"產物中繼資料欄位重複：{key}")
        result[key] = value
    return result


def validate_artifact_metadata(
    policy: dict[str, object], evidence: dict[str, object]
) -> None:
    metadata = load_metadata(METADATA)
    board = policy["boards"]["bananapim6"]
    expected = {
        "board": "bananapim6",
        "release": "trixie",
        "branch": "legacy",
        "profile": "cli",
        "source_commit": evidence["source_commit"],
        "source_tree": evidence["source_tree"],
        "validation_config_sha256": evidence["build_validation_config_sha256"],
        "source_contract_projection_sha256": evidence[
            "source_contract_projection_sha256"
        ],
        "source_date_epoch": str(policy["source_date_epoch"]),
        "raw_size": str(evidence["image"]["size"]),
        "raw_sha256": evidence["image"]["sha256"],
        "xz_size": str(evidence["archive"]["size"]),
        "xz_sha256": evidence["archive"]["sha256"],
        "linux_git_source": policy["linux_source"],
        "linux_git_ref": policy["linux_ref"],
        "linux_revision": policy["linux_commit"],
        "uboot_git_source": board["uboot_git_source"],
        "uboot_git_ref": board["uboot_git_ref"],
        "uboot_revision": board["uboot_revision"],
        "firmware_git_source": policy["firmware_source"],
        "firmware_git_ref": policy["firmware_ref"],
        "firmware_revision": policy["firmware_commit"],
    }
    for key, value in expected.items():
        require(metadata.get(key) == str(value), f"產物中繼資料 {key} 不符")


def load_live_material_evidence(
    policy: dict[str, object], evidence_level: str = "L2"
) -> dict[str, object]:
    require(evidence_level in {"L1", "L2"}, "live 物質證據層級無效")
    row = load_candidate_row()
    image = resolve_matrix_artifact(row["img_path"], ".img", "L2 IMG")
    archive = resolve_matrix_artifact(row["xz_path"], ".img.xz", "L2 XZ")
    completion = load_json(COMPLETION_STATUS, "建置完成狀態")
    verification = load_json(VERIFICATION_STATUS, "共用驗證狀態")
    require(completion.get("status") == "complete", "建置完成狀態不是 complete")
    require(verification.get("status") == "complete", "共用驗證狀態不是 complete")
    require(
        verification.get("evidence_level") == evidence_level,
        f"共用驗證狀態不是 {evidence_level}",
    )
    require(verification.get("xz_stream_verified") is True, "共用驗證未確認 XZ 串流")
    final_config_paths = validate_final_config_manifest(policy)
    image_checks = inspect_read_only_image(policy, image, final_config_paths)
    projection = contract_projection_sha256(policy)
    require(
        completion.get("source_contract_projection_sha256") == projection,
        "建置完成狀態未綁定來源契約投影",
    )
    require(
        verification.get("source_contract_projection_sha256") == projection,
        "共用驗證狀態未綁定來源契約投影",
    )
    evidence: dict[str, object] = {
        "status": "complete",
        "evidence_level": evidence_level,
        "full_rootfs_image_built": True,
        "read_only_content_verified": True,
        "hardware_tested": False,
        "source_commit": row["source_commit"],
        "source_tree": completion.get("source_tree"),
        "verifier_commit": verification.get("verifier_commit"),
        "source_contract_projection_sha256": projection,
        "source_date_epoch": policy["source_date_epoch"],
        "build_validation_config_sha256": completion.get("validation_config_sha256"),
        "verification_config_sha256": verification.get("verification_config_sha256"),
        "candidate_matrix_sha256": file_sha256(MATRIX),
        "completion_status_sha256": file_sha256(COMPLETION_STATUS),
        "verification_manifest_sha256": validate_verification_manifest(
            evidence_level
        ),
        "uboot_payload_manifest_sha256": file_sha256(UBOOT_PAYLOAD_EVIDENCE),
        "final_config_manifest_sha256": file_sha256(FINAL_CONFIG_EVIDENCE),
        "xz_stream_verified": True,
        "verified_utc": verification.get("verified_utc"),
        "image": {
            "path": str(image.relative_to(ROOT)),
            "size": int(row["raw_size"]),
            "sha256": row["raw_sha256"],
        },
        "archive": {
            "path": str(archive.relative_to(ROOT)),
            "size": int(row["xz_size"]),
            "sha256": row["xz_sha256"],
        },
        "linux_dtb": image_checks["linux_dtb"],
    }
    validate_artifact_metadata(policy, evidence)
    return evidence


def validate_calibration_evidence(policy: dict[str, object]) -> dict[str, object]:
    require(policy.get("candidate_level") == "L1 元件候選", "M6 校準只接受 L1 契約")
    evidence = load_live_material_evidence(policy, "L1")
    validate_l2_git_evidence(policy, evidence)
    image = resolve_matrix_artifact(
        Path(evidence["image"]["path"])
        .relative_to(OUTPUT_DIR.relative_to(ROOT))
        .as_posix(),
        ".img",
        "L1 校準 IMG",
    )
    archive = resolve_matrix_artifact(
        Path(evidence["archive"]["path"])
        .relative_to(OUTPUT_DIR.relative_to(ROOT))
        .as_posix(),
        ".img.xz",
        "L1 校準 XZ",
    )
    for name, path in (("image", image), ("archive", archive)):
        artifact = evidence[name]
        require(path.stat().st_size == artifact["size"], f"L1 校準 {name} 大小不符")
        require(file_sha256(path) == artifact["sha256"], f"L1 校準 {name} 雜湊不符")
    validate_xz_stream_matches_image(image, archive, evidence)

    row = load_candidate_row()
    require(row["source_commit"] == evidence["source_commit"], "L1 校準矩陣來源提交不符")
    require(file_sha256(MATRIX) == evidence["candidate_matrix_sha256"], "L1 校準矩陣雜湊不符")
    completion = load_json(COMPLETION_STATUS, "L1 校準建置完成狀態")
    verification = load_json(VERIFICATION_STATUS, "L1 校準共用驗證狀態")
    require(file_sha256(COMPLETION_STATUS) == evidence["completion_status_sha256"], "L1 校準建置狀態雜湊不符")
    for key, completion_key in (
        ("source_commit", "source_commit"),
        ("source_tree", "source_tree"),
        ("build_validation_config_sha256", "validation_config_sha256"),
        ("candidate_matrix_sha256", "candidates_sha256"),
        ("source_date_epoch", "source_date_epoch"),
        ("source_contract_projection_sha256", "source_contract_projection_sha256"),
    ):
        require(completion.get(completion_key) == evidence[key], f"L1 校準建置狀態 {completion_key} 不符")
    for key in (
        "source_commit",
        "source_tree",
        "verifier_commit",
        "build_validation_config_sha256",
        "verification_config_sha256",
        "candidate_matrix_sha256",
        "completion_status_sha256",
        "source_date_epoch",
        "source_contract_projection_sha256",
        "uboot_payload_manifest_sha256",
        "final_config_manifest_sha256",
    ):
        require(verification.get(key) == evidence[key], f"L1 校準驗證狀態 {key} 不符")
    require(verification.get("evidence_level") == "L1", "L1 校準驗證狀態層級不符")
    require(verification.get("xz_stream_verified") is True, "L1 校準未綁定 XZ 串流")
    require(
        validate_verification_manifest("L1") == evidence["verification_manifest_sha256"],
        "L1 校準共用驗證清單雜湊不符",
    )

    final_config_paths = validate_final_config_manifest(policy)
    overlap_summary = validate_payload_overlap_manifest(policy, image)
    partition_summary = validate_dual_partition_contract(policy, image)
    image_checks = inspect_read_only_image(policy, image, final_config_paths)
    require(image_checks["linux_dtb"] == evidence["linux_dtb"], "L1 校準 DTB 二次讀回不符")
    return {
        "schema_version": 1,
        "status": "calibration_complete",
        "evidence_level": "L1",
        "source_commit": evidence["source_commit"],
        "source_tree": evidence["source_tree"],
        "verifier_commit": evidence["verifier_commit"],
        "source_contract_projection_sha256": evidence[
            "source_contract_projection_sha256"
        ],
        "source_date_epoch": evidence["source_date_epoch"],
        "image": evidence["image"],
        "archive": evidence["archive"],
        "linux_dtb": evidence["linux_dtb"],
        "candidate_matrix_sha256": evidence["candidate_matrix_sha256"],
        "completion_status_sha256": evidence["completion_status_sha256"],
        "common_verification_manifest_sha256": evidence[
            "verification_manifest_sha256"
        ],
        "common_verification_status_sha256": file_sha256(VERIFICATION_STATUS),
        "uboot_payload_manifest_sha256": evidence[
            "uboot_payload_manifest_sha256"
        ],
        "final_config_manifest_sha256": evidence["final_config_manifest_sha256"],
        "checks": {
            "partition_table": partition_summary,
            "payload_overlap": overlap_summary,
            "read_only_image": image_checks,
        },
    }


def write_calibration_completion(record: dict[str, object]) -> None:
    temporary = Path(f"{CALIBRATION_EVIDENCE}.partial")
    temporary.write_bytes(material_record_bytes(record))
    os.replace(temporary, CALIBRATION_EVIDENCE)
    stored = load_json(CALIBRATION_EVIDENCE, "M6 校準證據")
    require(stored == record, "M6 校準證據原子寫入後讀回不一致")


def validate_l2_material_evidence(
    policy: dict[str, object], evidence_source: str
) -> dict[str, object]:
    require(policy.get("candidate_level") == "L2 內部軟體候選", "L1 校準狀態不得執行 L2 物質驗證")
    require(evidence_source in {"historical", "live"}, "L2 證據來源無效")
    evidence = (
        load_live_material_evidence(policy)
        if evidence_source == "live"
        else deepcopy(policy.get("image_build_evidence"))
    )
    require(isinstance(evidence, dict), "L2 缺少版本控制內映像證據")
    validate_l2_git_evidence(policy, evidence)
    image = resolve_matrix_artifact(
        Path(evidence["image"]["path"]).relative_to(OUTPUT_DIR.relative_to(ROOT)).as_posix(),
        ".img",
        "L2 IMG",
    )
    archive = resolve_matrix_artifact(
        Path(evidence["archive"]["path"]).relative_to(OUTPUT_DIR.relative_to(ROOT)).as_posix(),
        ".img.xz",
        "L2 XZ",
    )
    for name, path in (("image", image), ("archive", archive)):
        artifact = evidence[name]
        require(path.stat().st_size == artifact["size"], f"L2 {name} 大小與實檔不符")
        require(file_sha256(path) == artifact["sha256"], f"L2 {name} 雜湊與實檔不符")
    validate_xz_stream_matches_image(image, archive, evidence)

    row = load_candidate_row()
    require(row["source_commit"] == evidence["source_commit"], "候選矩陣來源提交不符")
    require(row["raw_size"] == str(evidence["image"]["size"]), "候選矩陣 IMG 大小不符")
    require(row["raw_sha256"] == evidence["image"]["sha256"], "候選矩陣 IMG 雜湊不符")
    require(row["xz_size"] == str(evidence["archive"]["size"]), "候選矩陣 XZ 大小不符")
    require(row["xz_sha256"] == evidence["archive"]["sha256"], "候選矩陣 XZ 雜湊不符")
    require(file_sha256(MATRIX) == evidence["candidate_matrix_sha256"], "候選矩陣雜湊不符")

    completion = load_json(COMPLETION_STATUS, "建置完成狀態")
    verification = load_json(VERIFICATION_STATUS, "共用驗證狀態")
    require(completion.get("status") == "complete", "L2 建置狀態不是 complete")
    require(verification.get("status") == "complete", "L2 驗證狀態不是 complete")
    require(verification.get("evidence_level") == "L2", "L2 驗證狀態層級不符")
    require(file_sha256(COMPLETION_STATUS) == evidence["completion_status_sha256"], "建置狀態雜湊不符")
    for key, completion_key in (
        ("source_commit", "source_commit"),
        ("source_tree", "source_tree"),
        ("build_validation_config_sha256", "validation_config_sha256"),
        ("candidate_matrix_sha256", "candidates_sha256"),
        ("source_date_epoch", "source_date_epoch"),
    ):
        require(completion.get(completion_key) == evidence[key], f"建置狀態 {completion_key} 不符")
    for key in (
        "source_commit",
        "source_tree",
        "verifier_commit",
        "build_validation_config_sha256",
        "verification_config_sha256",
        "candidate_matrix_sha256",
        "completion_status_sha256",
        "source_date_epoch",
        "uboot_payload_manifest_sha256",
        "final_config_manifest_sha256",
    ):
        require(verification.get(key) == evidence[key], f"共用驗證狀態 {key} 不符")
    require(verification.get("xz_stream_verified") is True, "共用驗證未綁定 XZ 串流")
    require(verification.get("verified_utc") == evidence.get("verified_utc"), "共用驗證時間不符")
    require(validate_verification_manifest() == evidence["verification_manifest_sha256"], "共用驗證清單雜湊不符")

    final_config_paths = validate_final_config_manifest(policy)
    require(file_sha256(FINAL_CONFIG_EVIDENCE) == evidence["final_config_manifest_sha256"], "最終設定清單雜湊不符")
    overlap_summary = validate_payload_overlap_manifest(policy, image)
    require(file_sha256(UBOOT_PAYLOAD_EVIDENCE) == evidence["uboot_payload_manifest_sha256"], "U-Boot 載荷清單雜湊不符")
    partition_summary = validate_dual_partition_contract(policy, image)
    image_checks = inspect_read_only_image(policy, image, final_config_paths)
    require(image_checks["linux_dtb"] == evidence["linux_dtb"], "映像 DTB 與 L2 證據不符")
    validate_artifact_metadata(policy, evidence)
    return {
        "schema_version": 1,
        "status": "complete",
        "evidence_level": "L2",
        "source_commit": evidence["source_commit"],
        "source_tree": evidence["source_tree"],
        "verifier_commit": evidence["verifier_commit"],
        "source_contract_projection_sha256": evidence[
            "source_contract_projection_sha256"
        ],
        "source_date_epoch": evidence["source_date_epoch"],
        "image": evidence["image"],
        "archive": evidence["archive"],
        "linux_dtb": evidence["linux_dtb"],
        "candidate_matrix_sha256": evidence["candidate_matrix_sha256"],
        "completion_status_sha256": evidence["completion_status_sha256"],
        "common_verification_manifest_sha256": evidence["verification_manifest_sha256"],
        "common_verification_status_sha256": file_sha256(VERIFICATION_STATUS),
        "uboot_payload_manifest_sha256": evidence["uboot_payload_manifest_sha256"],
        "final_config_manifest_sha256": evidence["final_config_manifest_sha256"],
        "checks": {
            "partition_table": partition_summary,
            "payload_overlap": overlap_summary,
            "read_only_image": image_checks,
        },
    }


def material_record_bytes(record: dict[str, object]) -> bytes:
    return (
        json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def write_material_completion(record: dict[str, object]) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    evidence_temporary = Path(f"{MATERIAL_EVIDENCE}.partial")
    evidence_temporary.write_bytes(material_record_bytes(record))
    os.replace(evidence_temporary, MATERIAL_EVIDENCE)
    status = {
        "status": "complete",
        "evidence_level": "L2",
        "source_commit": record["source_commit"],
        "verifier_commit": record["verifier_commit"],
        "source_contract_projection_sha256": record[
            "source_contract_projection_sha256"
        ],
        "source_date_epoch": record["source_date_epoch"],
        "common_verification_status_sha256": record["common_verification_status_sha256"],
        "material_evidence_sha256": file_sha256(MATERIAL_EVIDENCE),
        "finalized_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    status_temporary = Path(f"{MATERIAL_STATUS}.partial")
    status_temporary.write_text(
        json.dumps(status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(status_temporary, MATERIAL_STATUS)


def validate_material_completion(record: dict[str, object]) -> None:
    stored = load_json(MATERIAL_EVIDENCE, "M6 物質證據")
    require(stored == record, "M6 物質證據與本次實檔重查結果不符")
    status = load_json(MATERIAL_STATUS, "M6 物質完成狀態")
    expected = {
        "status": "complete",
        "evidence_level": "L2",
        "source_commit": record["source_commit"],
        "verifier_commit": record["verifier_commit"],
        "source_contract_projection_sha256": record[
            "source_contract_projection_sha256"
        ],
        "source_date_epoch": record["source_date_epoch"],
        "common_verification_status_sha256": record["common_verification_status_sha256"],
        "material_evidence_sha256": file_sha256(MATERIAL_EVIDENCE),
    }
    for key, value in expected.items():
        require(status.get(key) == value, f"M6 物質完成狀態 {key} 不符")
    require(isinstance(status.get("finalized_utc"), str) and status["finalized_utc"], "M6 物質完成狀態缺少完成時間")


def main() -> None:
    global VERIFICATION_STATUS
    parser = argparse.ArgumentParser(description="檢查 BPI-M6 固定來源與候選證據契約")
    parser.add_argument(
        "config",
        nargs="?",
        type=Path,
        default=DEFAULT_CONFIG,
        help="驗證契約路徑；未指定時使用倉庫內固定契約",
    )
    parser.add_argument(
        "--phase",
        choices=("source-contract", "calibration", "material-evidence"),
        default="source-contract",
        help="建置前只檢查來源契約；提升或稽核時檢查完整物質證據",
    )
    parser.add_argument(
        "--evidence-source",
        choices=("historical", "live"),
        help="明確選擇版本控制內歷史證據，或從本次產物即時重建證據",
    )
    parser.add_argument(
        "--finalize-material-status",
        action="store_true",
        help="即時物質重查通過後原子寫入證據與完成狀態",
    )
    parser.add_argument(
        "--finalize-calibration",
        action="store_true",
        help="L1 live 校準通過後原子寫入校準證據",
    )
    parser.add_argument(
        "--status",
        type=Path,
        help="live 模式必須指定延後中的共用驗證狀態；historical 使用正式狀態",
    )
    parser.add_argument(
        "--print-source-contract-projection-sha256",
        action="store_true",
        help="來源契約檢查通過後只輸出穩定投影雜湊",
    )
    arguments = parser.parse_args()
    config_path = arguments.config
    data = load_json(config_path, "M6 驗證契約")
    board_text = BOARD.read_text(encoding="utf-8")
    family_text = FAMILY.read_text(encoding="utf-8")
    status = load_json(STATUS, "中央最佳化狀態")
    board = data["boards"]["bananapim6"]
    material_phase = arguments.phase == "material-evidence"
    calibration_phase = arguments.phase == "calibration"
    live_phase = material_phase or calibration_phase
    require(
        (material_phase and arguments.evidence_source is not None)
        or (calibration_phase and arguments.evidence_source == "live")
        or (not live_phase and arguments.evidence_source is None),
        "物質驗證必須選擇 historical 或 live；校準只接受 live；來源契約不得指定證據來源",
    )
    require(
        not arguments.finalize_material_status
        or (material_phase and arguments.evidence_source == "live"),
        "只有即時物質驗證可以寫入完成狀態",
    )
    require(
        not arguments.finalize_calibration
        or (calibration_phase and arguments.evidence_source == "live"),
        "只有 L1 live 校準可以寫入校準證據",
    )
    expected_live_status = Path(f"{OUTPUT_DIR / 'VERIFICATION_STATUS.json'}.partial")
    expected_historical_status = OUTPUT_DIR / "VERIFICATION_STATUS.json"
    if live_phase and arguments.evidence_source == "live":
        require(arguments.status is not None, "live 物質驗證必須指定延後中的共用狀態")
        require(
            arguments.status.resolve() == expected_live_status.resolve(),
            "live 驗證狀態路徑不是固定 .partial",
        )
        VERIFICATION_STATUS = arguments.status.resolve()
    elif material_phase:
        require(
            arguments.status is None
            or arguments.status.resolve() == expected_historical_status.resolve(),
            "historical 物質驗證只接受正式共用狀態",
        )
        VERIFICATION_STATUS = expected_historical_status
    else:
        require(arguments.status is None, "來源契約階段不得指定共用驗證狀態")
    require(
        not arguments.print_source_contract_projection_sha256 or not live_phase,
        "校準或物質驗證階段不得只輸出來源契約投影",
    )
    if material_phase and arguments.evidence_source == "historical":
        require(
            config_path.resolve() == DEFAULT_CONFIG.resolve(),
            "歷史物質驗證只接受版本控制內固定 M6 契約",
        )
        git_output("ls-files", "--error-unmatch", VALIDATION_RELATIVE)
    require_material_binding = material_phase and arguments.evidence_source == "historical"

    require(
        os.environ.get("PUBLIC_RELEASE", "no").lower() not in {"1", "true", "yes"},
        "此候選禁止公開發布",
    )
    require(
        os.environ.get("HARDWARE_CLAIMS", "no").lower() not in {"1", "true", "yes"},
        "此候選禁止硬體通過聲明",
    )

    require(data.get("schema_version") == 1, "驗證契約版本不符")
    require(data.get("candidate_branch") == "legacy", "候選分支不是 legacy")
    require(data.get("kernel_family") == "vs680", "核心家族不符")
    require(data.get("target_evidence_level") == "L2", "目標證據等級不符")
    require(data.get("source_date_epoch") == 1717001894, "來源時間戳不符")
    validate_contract_projection(data, require_material_binding)
    validate_candidate_state(
        data, status, require_material_binding=require_material_binding
    )

    expected_sources = {
        "linux": ("https://github.com/BPI-SINOVOIP/pi-linux.git", LINUX_REVISION),
        "uboot": ("https://github.com/BPI-SINOVOIP/pi-u-boot.git", UBOOT_REVISION),
        "firmware": ("https://github.com/armbian/firmware", FIRMWARE_REVISION),
    }
    for component, (source, revision) in expected_sources.items():
        entry = data["source_commits"][component]
        require(entry.get("source") == source, f"{component} 來源不符")
        require(entry.get("ref") == f"commit:{revision}", f"{component} 引用未固定")
        require(entry.get("revision") == revision, f"{component} 提交不符")
    require(data.get("linux_source") == expected_sources["linux"][0], "Linux 頂層來源不符")
    require(data.get("linux_ref") == f"commit:{LINUX_REVISION}", "Linux 頂層引用不符")
    require(data.get("linux_commit") == LINUX_REVISION, "Linux 頂層提交不符")
    require(data.get("firmware_source") == expected_sources["firmware"][0], "韌體頂層來源不符")
    require(data.get("firmware_ref") == f"commit:{FIRMWARE_REVISION}", "韌體頂層引用不符")
    require(data.get("firmware_commit") == FIRMWARE_REVISION, "韌體頂層提交不符")
    require(data.get("verify_firmware_source_resolution") is True, "未啟用韌體來源解析守門")

    board_requirements = (
        'ARMBIAN_FIRMWARE_GIT_SOURCE_BOARD="https://github.com/armbian/firmware"',
        f'ARMBIAN_FIRMWARE_GIT_REF_BOARD="commit:{FIRMWARE_REVISION}"',
        'declare -g ARMBIAN_FIRMWARE_GIT_SOURCE="${ARMBIAN_FIRMWARE_GIT_SOURCE_BOARD}"',
        'declare -g ARMBIAN_FIRMWARE_GIT_REF="${ARMBIAN_FIRMWARE_GIT_REF_BOARD}"',
        f"declare -g BOOTBRANCH='commit:{UBOOT_REVISION}'",
        f"declare -g KERNELBRANCH='commit:{LINUX_REVISION}'",
    )
    for requirement in board_requirements:
        require(requirement in board_text, f"板檔缺少固定設定：{requirement}")
    require("branch:" not in board_text, "板檔仍含可移動來源分支")

    require(board.get("partition_table") == "msdos", "分割表不是 MBR")
    require(
        board.get("required_partitions")
        == ["1:*:204800:524288", "2:*:729088:*"],
        "雙分割區契約不符",
    )
    require(board.get("required_partition_types") == ["1:ea", "2:83"], "MBR 分割區類型不符")
    require(board.get("boot_partition_number") == 1, "boot 分割區編號不符")
    require(board.get("root_partition_number") == 2, "root 分割區編號不符")
    require(board.get("boot_partition_label") == "BPI-BOOT", "boot 標籤不符")
    require(board.get("root_partition_label") == "BPI-ROOT", "root 標籤不符")
    require(
        board.get("boot_partition_filesystem_type") == "vfat",
        "boot 分割區檔案系統類型不符",
    )
    require(
        board.get("root_partition_filesystem_type") == "ext4",
        "root 分割區檔案系統類型不符",
    )
    require(board.get("boot_configuration") == "separate_fat_armbian_env", "boot 模式不符")
    require(board.get("boot_script_source") == "config/bootscripts/boot-vs680.cmd", "boot script 來源不符")
    require(
        board.get("boot_script_source_sha256")
        == data["source_file_sha256"]["config/bootscripts/boot-vs680.cmd"],
        "boot script 契約雜湊不一致",
    )

    require(
        board.get("final_kernel_config_sha256") == FINAL_KERNEL_CONFIG,
        "最終核心設定雜湊不符",
    )
    require(
        board.get("final_uboot_config_sha256") == FINAL_UBOOT_CONFIG,
        "最終 U-Boot 設定雜湊不符",
    )
    require(
        board.get("uboot_payloads")
        == ["bpi-m6-tzk-4MB.bin@512", "u-boot.bin@2097152"],
        "payload 位移契約不符",
    )
    require(
        board.get("payload_write_order")
        == ["bpi-m6-tzk-4MB.bin", "u-boot.bin"],
        "payload 寫入順序不符",
    )
    tzk_write = 'dd if="${tzk_payload}" of="$2" bs=512 seek=1'
    uboot_write = 'dd if="$1/u-boot.bin" of="$2" bs=1k seek=2048'
    require(tzk_write in family_text and uboot_write in family_text, "VS680 family 缺少受控 payload 寫入")
    require(family_text.index(tzk_write) < family_text.index(uboot_write), "VS680 family 的 payload 寫入順序不符")
    overlap = board.get("payload_overlap_policy", {})
    require(overlap.get("allowed") is True, "未啟用受控 payload 重疊")
    require(overlap.get("earlier_payload") == "bpi-m6-tzk-4MB.bin", "先寫 payload 不符")
    require(overlap.get("later_payload") == "u-boot.bin", "後寫 payload 不符")
    require(overlap.get("overlap_starts_at_image_offset") == 2097152, "重疊起點不符")
    sizes = assignments(board.get("uboot_payload_sizes"), "payload 精確大小")
    hashes = assignments(board.get("uboot_payload_sha256"), "payload 雜湊")
    require(sizes == {"bpi-m6-tzk-4MB.bin": "4193792", "u-boot.bin": "616575"}, "payload 精確大小不符")
    require(hashes == {"bpi-m6-tzk-4MB.bin": TZK_SHA256, "u-boot.bin": UBOOT_SHA256}, "payload 雜湊不符")
    tzk = data["opaque_boot_payloads"]["packages/blobs/vs680/bpi-m6-tzk-4MB.bin"]
    require(tzk.get("size") == 4193792 and tzk.get("sha256") == TZK_SHA256, "TZK 契約不符")

    component = data["component_build_evidence"]
    require(valid_commit(component.get("source_commit")), "元件來源提交格式不符")
    for key in (
        "manifest_sha256",
        "verification_sha256",
        "uboot_log_sha256",
        "kernel_log_sha256",
        "dtb_sha256",
        "uboot_sha256",
    ):
        require(valid_sha256(component.get(key)), f"元件證據 {key} 格式不符")
    require(component.get("dtb_sha256") == COMPONENT_DTB, "元件 DTB 證據不符")
    require(component.get("uboot_sha256") == UBOOT_SHA256, "元件 U-Boot 證據不符")

    for relative, expected in data["source_file_sha256"].items():
        path = ROOT / relative
        require(path.is_file(), f"受控來源檔不存在：{relative}")
        require(file_sha256(path) == expected, f"受控來源檔雜湊不符：{relative}")

    calibration_record = (
        validate_calibration_evidence(data) if calibration_phase else {}
    )
    material_record = (
        validate_l2_material_evidence(data, arguments.evidence_source)
        if material_phase
        else {}
    )
    if calibration_phase:
        if arguments.finalize_calibration:
            write_calibration_completion(calibration_record)
        else:
            stored_calibration = load_json(CALIBRATION_EVIDENCE, "M6 校準證據")
            require(
                stored_calibration == calibration_record,
                "M6 校準證據與本次實檔重查結果不符",
            )
    if material_phase and arguments.evidence_source == "live":
        if arguments.finalize_material_status:
            write_material_completion(material_record)
        validate_material_completion(material_record)

    if arguments.print_source_contract_projection_sha256:
        print(contract_projection_sha256(data))
    else:
        phase = (
            "完整物質證據"
            if material_phase
            else "L1 校準證據"
            if calibration_phase
            else "建置前來源契約"
        )
        print(f"BPI-M6 固定來源、啟動鏈與{phase}檢查通過。")


if __name__ == "__main__":
    main()
