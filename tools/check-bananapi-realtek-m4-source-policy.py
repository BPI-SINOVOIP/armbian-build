#!/usr/bin/env python3
"""檢查 Banana Pi M4 固定來源與證據邊界。"""

from __future__ import annotations

import argparse
from copy import deepcopy
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import struct
import subprocess
import sys


EXPECTED_BSP = "25f5b88ec4ba34029f964693dc34028b26e6c67c"
EXPECTED_FIRMWARE = "f50a2a21bcdb77a562b3976930c5c6b521a1df08"
EXPECTED_OUTPUT = "output/components/2026.08/bananapi-realtek-rtd1395-m4-legacy"
EXPECTED_FINAL_KERNEL_CONFIG = "926ff6a7b7d22f32b85bdffd335e84b6c972c25626b8a493960622a056eb0a54"
ROOT = Path(__file__).resolve().parents[1]
VALIDATION_RELATIVE = "config/validation/bananapi-realtek-rtd1395-m4-legacy.json"
DEFAULT_CONFIG = ROOT / VALIDATION_RELATIVE
IMAGE_OUTPUT_RELATIVE = "output/images/2026.08/bananapi-realtek-rtd1395-m4-trixie-legacy-cli"
IMAGE_OUTPUT = ROOT / IMAGE_OUTPUT_RELATIVE
EXPECTED_CONDITIONAL_UNLINKED = {
    "u-boot-rtk/static_lib/libefuse.a.32",
    "u-boot-rtk/static_lib/libsha1_util.a.32",
    "u-boot-rtk/static_lib/libsecurity.a.32",
    "u-boot-rtk/static_lib/libkeyset.a.32",
}
EXPECTED_LINKED_UNREBUILT = {
    "u-boot-rtk/image/rtd1395/a_entry.img",
    "u-boot-rtk/image/rtd1395/exc_dispatch.img",
    "u-boot-rtk/image/rtd1395/exc_redirect.img",
    "u-boot-rtk/image/rtd1395/isr_video.img",
    "u-boot-rtk/image/rtd1395/ros_bootvector.img",
    "u-boot-rtk/image/rtd1395/v_entry.img",
}
EXPECTED_COMPONENTS = {
    "u-boot.bin",
    "uEnv.txt",
    "bluecore.audio",
    "Image",
    "rtd-1395-bananapi-m4-1GB.dtb",
    "rtd-1395-bananapi-m4-2GB.dtb",
    "linux.config",
    "linux-modules.tar.xz",
    "u-boot-link-command.txt",
}
CONTRACT_PROJECTION_EXCLUDED_TOP_LEVEL = {
    "candidate_level",
    "candidate_scope",
    "component_build_completed",
    "component_build_evidence",
    "current_evidence_level",
    "full_image_built",
    "full_rootfs_image_built",
    "image_build_evidence",
    "rootfs_image_built",
    "source_contract_projection_sha256",
}
CONTRACT_PROJECTION_EXCLUDED_BOARD_FIELDS = {
    "dtb_sha256_evidence_scope",
    "image_dtb_sha256",
}


def fail(message: str) -> None:
    raise SystemExit(f"M4 來源政策拒絕：{message}")


def require(condition: bool, message: str) -> None:
    if not condition:
        fail(message)


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def valid_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def git_output(*arguments: str) -> bytes:
    result = subprocess.run(
        ["git", "-C", str(ROOT), *arguments],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    require(result.returncode == 0, f"Git 證據不存在：{' '.join(arguments)}")
    return result.stdout


def load_json(path: Path, description: str) -> dict[str, object]:
    require(path.is_file(), f"缺少{description}：{path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        fail(f"{description}無法解析：{error}")
    require(isinstance(value, dict), f"{description}不是物件")
    return value


def load_single_tsv(
    path: Path, fields: list[str], description: str
) -> dict[str, str]:
    require(path.is_file(), f"缺少{description}：{path}")
    try:
        reader = csv.DictReader(io.StringIO(path.read_text(encoding="utf-8")), delimiter="\t")
        require(reader.fieldnames == fields, f"{description}欄位不符")
        rows = list(reader)
    except UnicodeError as error:
        fail(f"{description}無法解析：{error}")
    require(len(rows) == 1, f"{description}必須只有一筆資料")
    require(None not in rows[0], f"{description}含額外欄位")
    return rows[0]


def contract_projection(config: dict[str, object]) -> dict[str, object]:
    """建立排除候選狀態與實體映像證據的穩定來源契約。"""
    projection = deepcopy(config)
    for key in CONTRACT_PROJECTION_EXCLUDED_TOP_LEVEL:
        projection.pop(key, None)
    boards = projection.get("boards", {})
    if isinstance(boards, dict):
        for board in boards.values():
            if isinstance(board, dict):
                for key in CONTRACT_PROJECTION_EXCLUDED_BOARD_FIELDS:
                    board.pop(key, None)
    return projection


def contract_projection_sha256(config: dict[str, object]) -> str:
    encoded = json.dumps(
        contract_projection(config),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def validate_image_evidence(
    config: dict[str, object], evidence: dict[str, object]
) -> None:
    """確認版本控制內 L2 證據與原始建置提交互相綁定。"""
    require(evidence.get("status") == "complete", "L2 映像證據尚未完成")
    require(evidence.get("evidence_level") == "L2", "L2 映像證據層級不符")
    require(evidence.get("full_rootfs_image_built") is True, "L2 缺少完整 rootfs 證據")
    require(evidence.get("read_only_content_verified") is True, "L2 缺少唯讀內容驗證")
    require(evidence.get("hardware_tested") is False, "L2 不得冒充實機驗證")
    require(evidence.get("source_date_epoch") == 1711071187, "L2 來源時間基準不符")
    require(evidence.get("xz_stream_verified") is True, "L2 缺少 XZ 串流驗證")
    for field in ("source_commit", "source_tree", "verifier_commit"):
        require(
            isinstance(evidence.get(field), str)
            and len(evidence[field]) == 40
            and all(character in "0123456789abcdef" for character in evidence[field]),
            f"L2 {field} 格式不符",
        )
    require(
        evidence["source_commit"] == evidence["verifier_commit"],
        "L2 來源與驗證器提交不一致",
    )
    for field in (
        "source_contract_projection_sha256",
        "build_validation_config_sha256",
        "verification_config_sha256",
        "candidate_matrix_sha256",
        "completion_status_sha256",
        "verification_manifest_sha256",
        "uboot_payload_manifest_sha256",
        "final_config_manifest_sha256",
    ):
        require(valid_sha256(evidence.get(field)), f"L2 {field} 格式不符")
    require(
        evidence["build_validation_config_sha256"]
        == evidence["verification_config_sha256"],
        "L2 建置與驗證契約雜湊不一致",
    )

    source_commit = evidence["source_commit"]
    git_output("cat-file", "-e", f"{source_commit}^{{commit}}")
    ancestry = subprocess.run(
        ["git", "-C", str(ROOT), "merge-base", "--is-ancestor", source_commit, "HEAD"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    require(ancestry.returncode == 0, "L2 來源提交不是目前分支的祖先")
    source_tree = git_output("rev-parse", f"{source_commit}^{{tree}}").decode().strip()
    require(evidence["source_tree"] == source_tree, "L2 來源 tree 與提交不一致")
    validation_blob = git_output("show", f"{source_commit}:{VALIDATION_RELATIVE}")
    validation_sha256 = hashlib.sha256(validation_blob).hexdigest()
    require(
        evidence["build_validation_config_sha256"] == validation_sha256,
        "L2 建置契約雜湊與來源提交不一致",
    )
    try:
        source_config = json.loads(validation_blob.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        fail(f"L2 來源提交契約無法解析：{error}")
    projection = contract_projection_sha256(config)
    require(
        contract_projection_sha256(source_config) == projection,
        "L2 原始建置提交與現行來源契約投影不一致",
    )
    require(
        evidence["source_contract_projection_sha256"] == projection,
        "L2 證據未綁定來源契約投影",
    )

    board = config["boards"]["bananapim4"]
    linux_dtb = evidence.get("linux_dtb")
    require(isinstance(linux_dtb, dict), "L2 缺少映像 DTB 證據")
    require(linux_dtb.get("path") == board["dtb"], "L2 映像 DTB 路徑不符")
    require(linux_dtb.get("sha256") == board["dtb_sha256"], "L2 映像 DTB 雜湊不符")
    for name, suffix in (("image", ".img"), ("archive", ".img.xz")):
        artifact = evidence.get(name)
        require(isinstance(artifact, dict), f"L2 {name} 證據格式不符")
        relative = artifact.get("path")
        require(
            isinstance(relative, str)
            and relative.startswith(f"{IMAGE_OUTPUT_RELATIVE}/bananapim4/")
            and relative.endswith(suffix)
            and ".." not in Path(relative).parts,
            f"L2 {name} 路徑不合法",
        )
        require(
            isinstance(artifact.get("size"), int) and artifact["size"] > 0,
            f"L2 {name} 大小無效",
        )
        require(valid_sha256(artifact.get("sha256")), f"L2 {name} 雜湊格式不符")


def validate_historical_image(
    config: dict[str, object], evidence: dict[str, object]
) -> None:
    """以唯讀雜湊重新綁定已保存的正式 IMG、XZ 與驗證清單。"""
    require(IMAGE_OUTPUT.is_dir(), f"缺少 M4 固定正式輸出：{IMAGE_OUTPUT}")
    matrix_path = IMAGE_OUTPUT / "CANDIDATES.tsv"
    completion_path = IMAGE_OUTPUT / "COMPLETION_STATUS.json"
    verification_path = IMAGE_OUTPUT / "VERIFICATION.tsv"
    verification_status_path = IMAGE_OUTPUT / "VERIFICATION_STATUS.json"
    uboot_manifest_path = IMAGE_OUTPUT / "UBOOT_PAYLOAD_EVIDENCE.tsv"
    final_config_path = IMAGE_OUTPUT / "FINAL_CONFIG_EVIDENCE.tsv"

    row = load_single_tsv(
        matrix_path,
        [
            "board", "release", "profile", "raw_size", "raw_sha256", "xz_size",
            "xz_sha256", "img_path", "xz_path", "source_commit", "uboot_tag",
        ],
        "M4 候選矩陣",
    )
    require(
        (row["board"], row["release"], row["profile"], row["uboot_tag"])
        == ("bananapim4", "trixie", "cli", "v2015.07"),
        "M4 候選矩陣身分不符",
    )
    require(row["source_commit"] == evidence["source_commit"], "M4 候選矩陣來源提交不符")
    require(digest(matrix_path) == evidence["candidate_matrix_sha256"], "M4 候選矩陣雜湊不符")

    image = (IMAGE_OUTPUT / row["img_path"]).resolve()
    archive = (IMAGE_OUTPUT / row["xz_path"]).resolve()
    expected_parent = (IMAGE_OUTPUT / "bananapim4").resolve()
    require(image.parent == expected_parent and archive.parent == expected_parent, "M4 產物路徑逸出固定目錄")
    for name, path, artifact, size_field, digest_field in (
        ("IMG", image, evidence["image"], "raw_size", "raw_sha256"),
        ("XZ", archive, evidence["archive"], "xz_size", "xz_sha256"),
    ):
        require((ROOT / artifact["path"]).resolve() == path, f"M4 {name} 證據路徑不符")
        require(path.is_file(), f"缺少 M4 {name} 產物")
        require(path.stat().st_size == artifact["size"] == int(row[size_field]), f"M4 {name} 大小不符")
        require(digest(path) == artifact["sha256"] == row[digest_field], f"M4 {name} 雜湊不符")

    with image.open("rb") as stream:
        mbr = stream.read(512)
    require(len(mbr) == 512 and mbr[510:512] == b"\x55\xaa", "M4 IMG 缺少有效 MBR 簽章")
    partitions: list[tuple[int, int, int]] = []
    for index in range(2):
        entry = mbr[446 + index * 16 : 462 + index * 16]
        partitions.append((entry[4], *struct.unpack_from("<II", entry, 8)))
    require(
        partitions == [(0xEA, 8192, 524288), (0x83, 532480, 3620864)],
        "M4 IMG 的 MBR 雙分割區布局不符",
    )

    xz_test = subprocess.run(
        ["xz", "-t", "--", str(archive)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    require(xz_test.returncode == 0, "M4 XZ 結構或校驗碼不符")
    decompressed = subprocess.Popen(
        ["xz", "-dc", "--", str(archive)],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    decompressed_digest = hashlib.sha256()
    decompressed_size = 0
    assert decompressed.stdout is not None
    for block in iter(lambda: decompressed.stdout.read(1024 * 1024), b""):
        decompressed_digest.update(block)
        decompressed_size += len(block)
    decompressed.stdout.close()
    require(decompressed.wait() == 0, "M4 XZ 無法完整解壓")
    require(decompressed_size == evidence["image"]["size"], "M4 XZ 解壓大小與 IMG 不同")
    require(decompressed_digest.hexdigest() == evidence["image"]["sha256"], "M4 XZ 解壓內容與 IMG 不同")

    completion = load_json(completion_path, "M4 建置完成狀態")
    verification = load_json(verification_status_path, "M4 共用驗證狀態")
    require(completion.get("status") == "complete", "M4 建置完成狀態尚未閉合")
    require(digest(completion_path) == evidence["completion_status_sha256"], "M4 建置完成狀態雜湊不符")
    for field, completion_field in (
        ("source_commit", "source_commit"),
        ("source_tree", "source_tree"),
        ("build_validation_config_sha256", "validation_config_sha256"),
        ("candidate_matrix_sha256", "candidates_sha256"),
        ("source_contract_projection_sha256", "source_contract_projection_sha256"),
        ("source_date_epoch", "source_date_epoch"),
    ):
        require(completion.get(completion_field) == evidence[field], f"M4 建置狀態 {completion_field} 不符")
    for field in (
        "source_commit", "source_tree", "verifier_commit",
        "build_validation_config_sha256", "verification_config_sha256",
        "source_contract_projection_sha256", "candidate_matrix_sha256",
        "completion_status_sha256", "source_date_epoch",
        "uboot_payload_manifest_sha256", "final_config_manifest_sha256",
    ):
        require(verification.get(field) == evidence[field], f"M4 共用驗證狀態 {field} 不符")
    require(verification.get("status") == "complete", "M4 共用驗證尚未完成")
    require(verification.get("evidence_level") == "L2", "M4 共用驗證層級不符")
    require(verification.get("xz_stream_verified") is True, "M4 共用驗證未確認 XZ 串流")
    require(verification.get("verified_utc") == evidence["verified_utc"], "M4 共用驗證時間不符")
    for field in (
        "public_release_allowed", "hardware_claims_allowed", "hardware_validated",
        "opaque_payload_redistribution_verified", "toolchain_redistribution_verified",
    ):
        require(verification.get(field) is False, f"M4 共用驗證不得把 {field} 標為 true")

    verification_row = load_single_tsv(
        verification_path,
        ["board", "identity", "read_only_content", "evidence_level"],
        "M4 共用驗證清單",
    )
    require(
        verification_row
        == {"board": "bananapim4", "identity": "pass", "read_only_content": "pass", "evidence_level": "L2"},
        "M4 共用驗證清單結果不符",
    )
    require(digest(verification_path) == evidence["verification_manifest_sha256"], "M4 共用驗證清單雜湊不符")

    uboot_row = load_single_tsv(
        uboot_manifest_path,
        ["board", "payload", "placement", "offset", "size", "sha256"],
        "M4 U-Boot 載荷清單",
    )
    board = config["boards"]["bananapim4"]
    expected_uboot = {
        "board": "bananapim4", "payload": "u-boot.bin", "placement": "image",
        "offset": str(board["uboot_offset"]), "size": "521968",
        "sha256": board["uboot_payload_sha256"][0].split("=", 1)[1],
    }
    require(uboot_row == expected_uboot, "M4 U-Boot 載荷清單內容不符")
    require(digest(uboot_manifest_path) == evidence["uboot_payload_manifest_sha256"], "M4 U-Boot 載荷清單雜湊不符")
    with image.open("rb") as stream:
        stream.seek(board["uboot_offset"])
        payload = stream.read(int(uboot_row["size"]))
    require(hashlib.sha256(payload).hexdigest() == uboot_row["sha256"], "M4 IMG 內 U-Boot 載荷不符")

    final_row = load_single_tsv(
        final_config_path,
        ["board", "component", "path", "sha256"],
        "M4 最終設定清單",
    )
    require(
        final_row["board"] == "bananapim4"
        and final_row["component"] == "kernel"
        and final_row["path"] == "boot/config-4.9.119-legacy-realtek-rtd139x-bpi"
        and final_row["sha256"] == board["final_kernel_config_sha256"],
        "M4 最終核心設定清單內容不符",
    )
    require(digest(final_config_path) == evidence["final_config_manifest_sha256"], "M4 最終設定清單雜湊不符")

    metadata_path = IMAGE_OUTPUT / "bananapim4/artifact.metadata.txt"
    require(metadata_path.is_file(), "缺少 M4 產物中繼資料")
    metadata: dict[str, str] = {}
    for line in metadata_path.read_text(encoding="utf-8").splitlines():
        require("=" in line, "M4 產物中繼資料格式不符")
        key, value = line.split("=", 1)
        require(key and key not in metadata, "M4 產物中繼資料含空白或重複鍵")
        metadata[key] = value
    for key, value in (
        ("source_commit", evidence["source_commit"]),
        ("source_tree", evidence["source_tree"]),
        ("validation_config_sha256", evidence["build_validation_config_sha256"]),
        ("source_contract_projection_sha256", evidence["source_contract_projection_sha256"]),
        ("source_date_epoch", str(evidence["source_date_epoch"])),
        ("raw_sha256", evidence["image"]["sha256"]),
        ("xz_sha256", evidence["archive"]["sha256"]),
        ("evidence_level", "L2"),
    ):
        require(metadata.get(key) == value, f"M4 產物中繼資料 {key} 不符")


def verify_asset(source: Path, relative: str, metadata: dict[str, object]) -> None:
    path = source / relative
    require(path.is_file(), f"固定來源缺少資產：{relative}")
    require(path.stat().st_size == metadata["size"], f"資產大小不符：{relative}")
    require(digest(path) == metadata["sha256"], f"資產雜湊不符：{relative}")


def verify_source_tree(config: dict[str, object], source: Path) -> None:
    require((source / ".git").exists(), f"來源不是 Git 工作樹：{source}")
    revision = subprocess.run(
        ["git", "-C", str(source), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    require(revision == EXPECTED_BSP, "來源提交不符")

    verify_asset(
        source,
        str(config["linux_license_path"]),
        {"size": 18693, "sha256": config["linux_license_sha256"]},
    )
    verify_asset(
        source,
        str(config["uboot_license_path"]),
        {"size": (source / str(config["uboot_license_path"])).stat().st_size,
         "sha256": config["uboot_license_sha256"]},
    )
    toolchain = config["build_toolchain"]
    verify_asset(source, str(toolchain["path"]), toolchain)
    verify_asset(
        source,
        str(toolchain["manifest_path"]),
        {
            "size": (source / str(toolchain["manifest_path"])).stat().st_size,
            "sha256": toolchain["manifest_sha256"],
        },
    )
    for section in (
        "conditional_unlinked_prebuilt_assets",
        "linked_unrebuilt_source_assets",
        "runtime_prebuilt_assets",
        "excluded_source_assets",
    ):
        for relative, metadata in config[section].items():
            verify_asset(source, relative, metadata)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("契約", type=Path)
    parser.add_argument("固定來源目錄", nargs="?", type=Path)
    parser.add_argument(
        "--print-source-contract-projection-sha256",
        action="store_true",
        help="輸出排除狀態與映像證據後的來源契約投影雜湊",
    )
    parser.add_argument(
        "--verify-historical-image",
        action="store_true",
        help="重新核對版本控制內 L2 證據與固定正式 IMG、XZ 及清單",
    )
    arguments = parser.parse_args()
    config_path = arguments.契約.resolve()
    config = json.loads(config_path.read_text(encoding="utf-8"))
    require(
        not arguments.verify_historical_image or config_path == DEFAULT_CONFIG.resolve(),
        "歷史映像重驗只接受倉庫內固定 M4 契約",
    )
    require(
        not (
            arguments.verify_historical_image
            and arguments.print_source_contract_projection_sha256
        ),
        "歷史映像重驗不得只輸出來源契約投影",
    )

    require(config["schema_version"] == 1, "契約版本不支援")
    require(config["candidate_branch"] == "legacy", "候選分支必須為 legacy")
    require(config["kernel_family"] == "realtek-rtd139x-bpi", "核心家族不符")
    require(config["candidate_level"] == "L2 內部軟體候選", "M4 完整映像契約必須是內部 L2")
    require(config["candidate_scope"] == "internal-l2", "M4 候選範圍不符")
    require(config["current_evidence_level"] == "L2", "M4 候選證據層級不符")
    require(config["target_evidence_level"] == "L2", "M4 目標證據層級不符")
    family = config["realtek_family_inventory"]
    require(
        family["shared_legacy_include"] == "config/sources/families/include/realtek_bpi_legacy_common.inc",
        "Realtek legacy 共用入口不符",
    )
    require(family["shared_legacy_include_modified"] is True, "Realtek 共用入口的安全引號修正未登錄")
    require(family["legacy_boards"]["bananapim4"]["soc"] == "RTD1395", "M4 SoC 盤點不符")
    require(family["legacy_boards"]["bananapiw2"]["soc"] == "RTD1296", "W2 SoC 盤點不符")
    require(family["separate_family_boards"]["xpressreal-t3"]["soc"] == "RTD1619B", "RTD1619B 盤點不符")
    require(
        config["boards"]["bananapim4"]["final_kernel_config_sha256"]
        == EXPECTED_FINAL_KERNEL_CONFIG,
        "M4 最終核心設定校準雜湊不符",
    )
    require(config["source_date_epoch"] == 1711071187, "來源時間基準不符")
    for prefix in ("linux", "uboot"):
        require(config[f"{prefix}_commit"] == EXPECTED_BSP, f"{prefix} 提交不符")
        require(config[f"{prefix}_ref"] == f"commit:{EXPECTED_BSP}", f"{prefix} 不是精確提交")
        require(config[f"{prefix}_source"] == "https://github.com/BPI-SINOVOIP/BPI-M4-bsp.git", f"{prefix} 來源不符")
    require(config["firmware_commit"] == EXPECTED_FIRMWARE, "韌體提交不符")
    require(config["firmware_ref"] == f"commit:{EXPECTED_FIRMWARE}", "韌體不是精確提交")
    require(config["atf_applicable"] is False, "不得虛構獨立 TF-A 建置")
    require(config["verify_firmware_source_resolution"] is True, "完整映像必須核對韌體提交")
    projection = contract_projection_sha256(config)
    declared_projection = config.get("source_contract_projection_sha256")
    if declared_projection is not None:
        require(declared_projection == projection, "來源契約投影雜湊不符")

    require(
        set(config["conditional_unlinked_prebuilt_assets"]) == EXPECTED_CONDITIONAL_UNLINKED,
        "U-Boot 條件式未連結資產集合不符",
    )
    for metadata in config["conditional_unlinked_prebuilt_assets"].values():
        require(metadata["source_build_available"] is False, "條件式預建資產不得誤標可重建")
        require(metadata["included_in_candidate_binary"] is False, "未連結資產不得誤標進入候選")
        require(metadata["redistribution_license_verified"] is False, "條件式預建資產授權不得誤標已確認")
    require(
        set(config["linked_unrebuilt_source_assets"]) == EXPECTED_LINKED_UNREBUILT,
        "U-Boot 已嵌入但未重建資產集合不符",
    )
    for metadata in config["linked_unrebuilt_source_assets"].values():
        require(metadata["source_files_available"] is True, "已嵌入啟動段必須可追到來源檔")
        require(metadata["build_toolchain_pinned"] is False, "不得誤標輔助處理器工具鏈已固定")
        require(metadata["rebuilt_in_candidate"] is False, "不得誤標啟動段已由來源重建")
        require(metadata["included_in_candidate_binary"] is True, "實際嵌入資產不得誤標排除")
        require(metadata["redistribution_license_verified"] is False, "啟動段授權不得誤標已確認")
    for metadata in config["runtime_prebuilt_assets"].values():
        require(metadata["source_build_available"] is False, "執行期預建資產不得誤標可重建")
        require(metadata["redistribution_license_verified"] is False, "執行期預建資產授權不得誤標已確認")
    for metadata in config["excluded_source_assets"].values():
        require(metadata["included_in_candidate"] is False, "排除資產不得進入候選")
        require(metadata["redistribution_license_verified"] is False, "排除資產授權不得誤標已確認")

    require(config["public_release_allowed"] is False, "目前不得允許公開發布")
    require(config["candidate_public_release_approved"] is False, "目前不得標記發布審核通過")
    require(config["hardware_validation_complete"] is False, "目前沒有完整實機驗證")
    require(config["firmware_redistribution_license_verified"] is False, "韌體再散布授權不得誤標閉合")
    require(config["license_policy"]["opaque_payload_redistribution_verified"] is False, "不透明載荷授權不得誤標閉合")
    require(config["license_policy"]["toolchain_redistribution_verified"] is False, "工具鏈再散布授權不得誤標閉合")
    require(config["hardware_validated"] is False, "目前沒有實機證據")
    require(config["hardware_claims_allowed"] is False, "目前不得宣稱硬體通過")
    if config["full_image_built"]:
        require(config["rootfs_image_built"] is True, "完整映像缺少 rootfs 建置狀態")
        require(config["full_rootfs_image_built"] is True, "完整映像缺少 rootfs 證據")
        require(isinstance(config.get("image_build_evidence"), dict), "完整映像缺少機器證據")
        validate_image_evidence(config, config["image_build_evidence"])
    else:
        require(config["rootfs_image_built"] is False, "過渡契約不得誤標 rootfs 已建置")
        require(config["full_rootfs_image_built"] is False, "過渡契約不得誤標完整 rootfs")
        require("image_build_evidence" not in config, "過渡契約不得夾帶舊映像證據")
        require(not arguments.verify_historical_image, "尚未閉合完整映像，不能執行歷史重驗")
    require(os.environ.get("PUBLIC_RELEASE", "no") != "yes", "未完成授權前禁止公開發布")

    board = config["boards"]["bananapim4"]
    require(board["compatible"] == ["bananapi,bpi-m4", "realtek,rtd1395"], "板卡相容字串不符")
    require(board["memory_variants_mib"] == [1024, 2048], "記憶體變體契約不符")
    require(board["uboot_write_offset_bytes"] == 40960, "U-Boot 寫入位置不符")
    require(board["uboot_offset"] == 40960, "U-Boot 映像偏移不符")
    require(board["uboot_payload"] == "u-boot.bin", "U-Boot 載荷契約不符")
    require(board["partition_table"] == "msdos", "40 KiB 載荷必須搭配 MBR 契約")
    require(board["partition_start_sector"] == 8192, "FAT 開機分割區起點不符")
    require(board["root_partition_start_sector"] == 532480, "根分割區起點不符")
    require(board["root_filesystem_label"] == "BPI-ROOT", "根檔案系統標籤不符")
    require(board["root_partition_label"] == "BPI-ROOT", "根分割區標籤不符")
    require(board["boot_partition_label"] == "BPI-BOOT", "開機分割區標籤不符")
    require(board["boot_configuration"] == "realtek_bpi_uenv", "Realtek 開機設定類型不符")
    require(len(board["dtbs"]) == 2, "必須保留 1 GiB 與 2 GiB DTB")
    require(board["vendor_boot_dtbs"] == board["dtbs"], "vendor boot 的雙 DTB 契約不符")
    require(board["storage_contract"]["pcie_node"] == "/pcie@98060000", "PCIe DT 節點不符")
    for section in ("storage_contract", "network_contract", "usb_contract", "display_contract", "io_contract", "wireless_contract"):
        require(section in board, f"缺少板級介面契約：{section}")
    require(board["display_contract"]["kernel_gpu_driver_verified"] is False, "不得以 DT 節點宣稱 GPU 驅動成立")
    require(board["display_contract"]["userspace_acceleration_verified"] is False, "不得宣稱使用者空間 GPU 加速成立")
    require(board["display_contract"]["video_decode_verified"] is False, "不得宣稱視訊硬解成立")

    for item in config["documentation_evidence"]:
        require(item["local_path"].startswith("/media/pi/SMCI/bpi/"), "文件證據必須是已知本機路徑")
        require(item["included_in_candidate"] is False, "外部文件不得封裝進候選")
        require(item["redistribution_license_verified"] is False, "外部文件授權不得誤標已確認")

    if config["component_build_completed"]:
        require(config["candidate_level"] == "L2 內部軟體候選", "L2 必須保留既有元件證據")
        require(config["candidate_scope"] == "internal-l2", "L2 候選範圍不符")
        evidence = config["component_build_evidence"]
        require(evidence["local_evidence_root"] == EXPECTED_OUTPUT, "可攜元件證據路徑不符")
        require(evidence["full_rootfs_image_built"] is False, "元件證據不得冒充完整映像")
        require(evidence["uboot_rebuild_hash_match"] is True, "U-Boot 重建雜湊必須一致")
        require(set(evidence["artifacts"]) == EXPECTED_COMPONENTS, "元件集合不完整")
        require(evidence["uboot_rebuild_sha256"] == evidence["artifacts"]["u-boot.bin"]["sha256"], "U-Boot 證據互相矛盾")
        require(evidence["source_revision"] == EXPECTED_BSP, "元件證據來源提交不符")
        require(evidence["work_size_kib"] <= 10 * 1024 * 1024, "元件工作目錄超過契約上限")
        require(evidence["uboot_warning_count"] >= 0, "U-Boot 警告數無效")
        require(evidence["linux_warning_count"] >= 0, "Linux 警告數無效")
        require(evidence["linked_unrebuilt_source_asset_count"] == 6, "已嵌入未重建資產數量不符")
        require(evidence["conditional_unlinked_prebuilt_asset_count"] == 4, "條件式未連結資產數量不符")
    else:
        require(config["candidate_level"] == "L0 來源契約", "未保存元件不得標示 L1")
        require(config["candidate_scope"] == "internal-source-only", "L0 候選範圍不符")
        require("component_build_evidence" not in config, "未完成建置不得保留元件證據")

    source_argument = arguments.固定來源目錄.resolve() if arguments.固定來源目錄 else None
    source_environment = os.environ.get("M4_SOURCE_DIR")
    source = source_argument or (Path(source_environment).resolve() if source_environment else None)
    if source is not None:
        verify_source_tree(config, source)

    if arguments.verify_historical_image:
        validate_historical_image(config, config["image_build_evidence"])

    if arguments.print_source_contract_projection_sha256:
        print(projection)
    elif arguments.verify_historical_image:
        print("M4 固定正式 IMG、XZ、清單與原始提交歷史重驗通過。")
    else:
        print("M4 固定來源、授權邊界與證據等級檢查通過。")


if __name__ == "__main__":
    main()
