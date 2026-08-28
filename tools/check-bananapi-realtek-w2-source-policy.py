#!/usr/bin/env python3
"""檢查 Banana Pi W2 固定來源、二進位資產與發布邊界。"""

from __future__ import annotations

import argparse
from copy import deepcopy
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import re
import struct
import subprocess


ROOT = Path(__file__).resolve().parents[1]
VALIDATION_RELATIVE = "config/validation/bananapi-realtek-rtd1296-w2-legacy.json"
DEFAULT_CONFIG = (
    ROOT / VALIDATION_RELATIVE
)
IMAGE_OUTPUT_RELATIVE = (
    "output/images/2026.08/bananapi-realtek-rtd1296-w2-trixie-legacy-cli"
)
IMAGE_OUTPUT = ROOT / IMAGE_OUTPUT_RELATIVE
BOARD = ROOT / "config/boards/bananapiw2.wip"
STATUS = ROOT / "config/bananapi-optimization-status.json"
FAMILY = (
    ROOT
    / "config/sources/families/include/realtek_bpi_legacy_common.inc"
)
UBOOT_TIMESTAMP_PATCH = (
    ROOT
    / "patch/u-boot/u-boot-realtek-rtd129x-bpi-legacy"
    / "0003-build-use-source-date-epoch.patch"
)
KERNEL_IDENTITY_PATCH = (
    ROOT
    / "patch/kernel/archive/realtek-rtd129x-bpi-4.9"
    / "0002-dts-identify-bananapi-w2.patch"
)
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
    "final_kernel_config_sha256",
    "image_dtb_sha256",
}


def fail(message: str) -> None:
    raise SystemExit(f"W2 來源政策拒絕：{message}")


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
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


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
        reader = csv.DictReader(
            io.StringIO(path.read_text(encoding="utf-8")), delimiter="\t"
        )
        require(reader.fieldnames == fields, f"{description}欄位不符")
        rows = list(reader)
    except UnicodeError as error:
        fail(f"{description}無法解析：{error}")
    require(len(rows) == 1, f"{description}必須只有一筆資料")
    require(None not in rows[0], f"{description}含額外欄位")
    return rows[0]


def contract_projection_sha256(config: dict[str, object]) -> str:
    projection = deepcopy(config)
    for key in CONTRACT_PROJECTION_EXCLUDED_TOP_LEVEL:
        projection.pop(key, None)
    for board in projection.get("boards", {}).values():
        for key in CONTRACT_PROJECTION_EXCLUDED_BOARD_FIELDS:
            board.pop(key, None)
    encoded = json.dumps(
        projection,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def validate_image_evidence(
    config: dict[str, object], evidence: dict[str, object]
) -> None:
    """確認版本控制內 L2 證據與正式建置提交互相綁定。"""
    require(evidence.get("status") == "complete", "L2 映像證據尚未完成")
    require(evidence.get("evidence_level") == "L2", "L2 映像證據層級不符")
    require(
        evidence.get("full_rootfs_image_built") is True,
        "L2 缺少完整 rootfs 證據",
    )
    require(
        evidence.get("read_only_content_verified") is True,
        "L2 缺少唯讀內容驗證",
    )
    require(evidence.get("hardware_tested") is False, "L2 不得冒充實機驗證")
    require(
        evidence.get("source_date_epoch") == 1571768256,
        "L2 來源時間基準不符",
    )
    require(evidence.get("xz_stream_verified") is True, "L2 缺少 XZ 串流驗證")
    for field in ("source_commit", "source_tree", "verifier_commit"):
        require(
            isinstance(evidence.get(field), str)
            and re.fullmatch(r"[0-9a-f]{40}", evidence[field]) is not None,
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
        [
            "git",
            "-C",
            str(ROOT),
            "merge-base",
            "--is-ancestor",
            source_commit,
            "HEAD",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    require(ancestry.returncode == 0, "L2 來源提交不是目前分支的祖先")
    source_tree = git_output("rev-parse", f"{source_commit}^{{tree}}").decode().strip()
    require(evidence["source_tree"] == source_tree, "L2 來源 tree 與提交不一致")
    validation_blob = git_output("show", f"{source_commit}:{VALIDATION_RELATIVE}")
    require(
        evidence["build_validation_config_sha256"]
        == hashlib.sha256(validation_blob).hexdigest(),
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

    board = config["boards"]["bananapiw2"]
    linux_dtb = evidence.get("linux_dtb")
    require(isinstance(linux_dtb, dict), "L2 缺少映像 DTB 證據")
    require(linux_dtb.get("path") == board["dtb"], "L2 映像 DTB 路徑不符")
    require(
        linux_dtb.get("sha256") == board["dtb_sha256"],
        "L2 映像 DTB 雜湊不符",
    )
    for name, suffix in (("image", ".img"), ("archive", ".img.xz")):
        artifact = evidence.get(name)
        require(isinstance(artifact, dict), f"L2 {name} 證據格式不符")
        relative = artifact.get("path")
        require(
            isinstance(relative, str)
            and relative.startswith(f"{IMAGE_OUTPUT_RELATIVE}/bananapiw2/")
            and relative.endswith(suffix)
            and ".." not in Path(relative).parts,
            f"L2 {name} 路徑不合法",
        )
        require(
            isinstance(artifact.get("size"), int) and artifact["size"] > 0,
            f"L2 {name} 大小無效",
        )
        require(
            valid_sha256(artifact.get("sha256")),
            f"L2 {name} 雜湊格式不符",
        )


def validate_historical_image(
    config: dict[str, object], evidence: dict[str, object]
) -> None:
    """以唯讀雜湊重新綁定已保存的正式 IMG、XZ 與驗證清單。"""
    require(IMAGE_OUTPUT.is_dir(), f"缺少 W2 固定正式輸出：{IMAGE_OUTPUT}")
    matrix_path = IMAGE_OUTPUT / "CANDIDATES.tsv"
    completion_path = IMAGE_OUTPUT / "COMPLETION_STATUS.json"
    verification_path = IMAGE_OUTPUT / "VERIFICATION.tsv"
    verification_status_path = IMAGE_OUTPUT / "VERIFICATION_STATUS.json"
    uboot_manifest_path = IMAGE_OUTPUT / "UBOOT_PAYLOAD_EVIDENCE.tsv"
    final_config_path = IMAGE_OUTPUT / "FINAL_CONFIG_EVIDENCE.tsv"

    row = load_single_tsv(
        matrix_path,
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
        "W2 候選矩陣",
    )
    require(
        (row["board"], row["release"], row["profile"], row["uboot_tag"])
        == ("bananapiw2", "trixie", "cli", "v2015.07"),
        "W2 候選矩陣身分不符",
    )
    require(
        row["source_commit"] == evidence["source_commit"],
        "W2 候選矩陣來源提交不符",
    )
    require(
        digest(matrix_path) == evidence["candidate_matrix_sha256"],
        "W2 候選矩陣雜湊不符",
    )

    image = (IMAGE_OUTPUT / row["img_path"]).resolve()
    archive = (IMAGE_OUTPUT / row["xz_path"]).resolve()
    expected_parent = (IMAGE_OUTPUT / "bananapiw2").resolve()
    require(
        image.parent == expected_parent and archive.parent == expected_parent,
        "W2 產物路徑逸出固定目錄",
    )
    for name, path, artifact, size_field, digest_field in (
        ("IMG", image, evidence["image"], "raw_size", "raw_sha256"),
        ("XZ", archive, evidence["archive"], "xz_size", "xz_sha256"),
    ):
        require(
            (ROOT / artifact["path"]).resolve() == path,
            f"W2 {name} 證據路徑不符",
        )
        require(path.is_file(), f"缺少 W2 {name} 產物")
        require(
            path.stat().st_size == artifact["size"] == int(row[size_field]),
            f"W2 {name} 大小不符",
        )
        require(
            digest(path) == artifact["sha256"] == row[digest_field],
            f"W2 {name} 雜湊不符",
        )

    with image.open("rb") as stream:
        mbr = stream.read(512)
    require(
        len(mbr) == 512 and mbr[510:512] == b"\x55\xaa",
        "W2 IMG 缺少有效 MBR 簽章",
    )
    partitions: list[tuple[int, int, int]] = []
    for index in range(2):
        entry = mbr[446 + index * 16 : 462 + index * 16]
        partitions.append((entry[4], *struct.unpack_from("<II", entry, 8)))
    require(image.stat().st_size % 512 == 0, "W2 IMG 大小未對齊邏輯磁區")
    expected_root_sectors = image.stat().st_size // 512 - 532480
    require(
        partitions
        == [(0xEA, 8192, 524288), (0x83, 532480, expected_root_sectors)],
        "W2 IMG 的 MBR 雙分割區布局不符",
    )

    xz_test = subprocess.run(
        ["xz", "-t", "--", str(archive)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    require(xz_test.returncode == 0, "W2 XZ 結構或校驗碼不符")
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
    require(decompressed.wait() == 0, "W2 XZ 無法完整解壓")
    require(
        decompressed_size == evidence["image"]["size"],
        "W2 XZ 解壓大小與 IMG 不同",
    )
    require(
        decompressed_digest.hexdigest() == evidence["image"]["sha256"],
        "W2 XZ 解壓內容與 IMG 不同",
    )

    completion = load_json(completion_path, "W2 建置完成狀態")
    verification = load_json(verification_status_path, "W2 共用驗證狀態")
    require(completion.get("status") == "complete", "W2 建置完成狀態尚未閉合")
    require(
        digest(completion_path) == evidence["completion_status_sha256"],
        "W2 建置完成狀態雜湊不符",
    )
    for field, completion_field in (
        ("source_commit", "source_commit"),
        ("source_tree", "source_tree"),
        ("build_validation_config_sha256", "validation_config_sha256"),
        ("candidate_matrix_sha256", "candidates_sha256"),
        ("source_contract_projection_sha256", "source_contract_projection_sha256"),
        ("source_date_epoch", "source_date_epoch"),
    ):
        require(
            completion.get(completion_field) == evidence[field],
            f"W2 建置狀態 {completion_field} 不符",
        )
    for field in (
        "source_commit",
        "source_tree",
        "verifier_commit",
        "build_validation_config_sha256",
        "verification_config_sha256",
        "source_contract_projection_sha256",
        "candidate_matrix_sha256",
        "completion_status_sha256",
        "source_date_epoch",
        "uboot_payload_manifest_sha256",
        "final_config_manifest_sha256",
    ):
        require(
            verification.get(field) == evidence[field],
            f"W2 共用驗證狀態 {field} 不符",
        )
    require(verification.get("status") == "complete", "W2 共用驗證尚未完成")
    require(
        verification.get("evidence_level") == "L2",
        "W2 共用驗證層級不符",
    )
    require(
        verification.get("xz_stream_verified") is True,
        "W2 共用驗證未確認 XZ 串流",
    )
    require(
        verification.get("verified_utc") == evidence["verified_utc"],
        "W2 共用驗證時間不符",
    )
    for field in (
        "public_release_allowed",
        "hardware_claims_allowed",
        "hardware_validated",
        "opaque_payload_redistribution_verified",
        "toolchain_redistribution_verified",
    ):
        require(
            verification.get(field) is False,
            f"W2 共用驗證不得把 {field} 標為 true",
        )

    verification_row = load_single_tsv(
        verification_path,
        ["board", "identity", "read_only_content", "evidence_level"],
        "W2 共用驗證清單",
    )
    require(
        verification_row
        == {
            "board": "bananapiw2",
            "identity": "pass",
            "read_only_content": "pass",
            "evidence_level": "L2",
        },
        "W2 共用驗證清單結果不符",
    )
    require(
        digest(verification_path) == evidence["verification_manifest_sha256"],
        "W2 共用驗證清單雜湊不符",
    )

    board = config["boards"]["bananapiw2"]
    uboot_row = load_single_tsv(
        uboot_manifest_path,
        ["board", "payload", "placement", "offset", "size", "sha256"],
        "W2 U-Boot 載荷清單",
    )
    expected_size = board["uboot_payload_sizes"][0].split("=", 1)[1]
    expected_sha256 = board["uboot_payload_sha256"][0].split("=", 1)[1]
    require(
        uboot_row
        == {
            "board": "bananapiw2",
            "payload": "u-boot.bin",
            "placement": "image",
            "offset": str(board["uboot_offset"]),
            "size": expected_size,
            "sha256": expected_sha256,
        },
        "W2 U-Boot 載荷清單內容不符",
    )
    require(
        digest(uboot_manifest_path) == evidence["uboot_payload_manifest_sha256"],
        "W2 U-Boot 載荷清單雜湊不符",
    )
    with image.open("rb") as stream:
        stream.seek(board["uboot_offset"])
        payload = stream.read(int(expected_size))
    require(
        hashlib.sha256(payload).hexdigest() == expected_sha256,
        "W2 IMG 內 U-Boot 載荷不符",
    )

    final_row = load_single_tsv(
        final_config_path,
        ["board", "component", "path", "sha256"],
        "W2 最終設定清單",
    )
    require(
        final_row["board"] == "bananapiw2"
        and final_row["component"] == "kernel"
        and final_row["path"]
        == "boot/config-4.9.119-legacy-realtek-rtd129x-bpi"
        and final_row["sha256"] == board["final_kernel_config_sha256"],
        "W2 最終核心設定清單內容不符",
    )
    require(
        digest(final_config_path) == evidence["final_config_manifest_sha256"],
        "W2 最終設定清單雜湊不符",
    )

    metadata_path = IMAGE_OUTPUT / "bananapiw2/artifact.metadata.txt"
    require(metadata_path.is_file(), "缺少 W2 產物中繼資料")
    metadata: dict[str, str] = {}
    for line in metadata_path.read_text(encoding="utf-8").splitlines():
        require("=" in line, "W2 產物中繼資料格式不符")
        key, value = line.split("=", 1)
        require(key and key not in metadata, "W2 產物中繼資料含空白或重複鍵")
        metadata[key] = value
    for key, value in (
        ("source_commit", evidence["source_commit"]),
        ("source_tree", evidence["source_tree"]),
        ("validation_config_sha256", evidence["build_validation_config_sha256"]),
        (
            "source_contract_projection_sha256",
            evidence["source_contract_projection_sha256"],
        ),
        ("source_date_epoch", str(evidence["source_date_epoch"])),
        ("raw_sha256", evidence["image"]["sha256"]),
        ("xz_sha256", evidence["archive"]["sha256"]),
        ("evidence_level", "L2"),
    ):
        require(metadata.get(key) == value, f"W2 產物中繼資料 {key} 不符")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("契約", nargs="?", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument(
        "--print-source-contract-projection-sha256",
        action="store_true",
        help="輸出排除狀態與實體映像證據後的來源契約投影雜湊",
    )
    parser.add_argument(
        "--verify-historical-image",
        action="store_true",
        help="重新核對版本控制內 L2 證據與固定正式 IMG、XZ 及清單",
    )
    arguments = parser.parse_args()
    config_path = arguments.契約.resolve()
    require(
        not arguments.verify_historical_image
        or config_path == DEFAULT_CONFIG.resolve(),
        "歷史映像重驗只接受倉庫內固定 W2 契約",
    )
    require(
        not (
            arguments.verify_historical_image
            and arguments.print_source_contract_projection_sha256
        ),
        "歷史映像重驗不得只輸出來源契約投影",
    )
    data = json.loads(config_path.read_text(encoding="utf-8"))
    board_text = BOARD.read_text(encoding="utf-8")
    status = json.loads(STATUS.read_text(encoding="utf-8"))
    family_text = FAMILY.read_text(encoding="utf-8")
    timestamp_patch = UBOOT_TIMESTAMP_PATCH.read_text(encoding="utf-8")
    identity_patch = KERNEL_IDENTITY_PATCH.read_text(encoding="utf-8")
    revision = "6e6aefc35dc50b1b8231cdb03a995d088f29eb21"
    firmware_revision = "f50a2a21bcdb77a562b3976930c5c6b521a1df08"

    require(data["schema_version"] == 1, "驗證契約版本不符")
    require(data["candidate_branch"] == "legacy", "候選分支不是 legacy")
    require(data["candidate_level"] == "L2 內部軟體候選", "候選層級不是內部 L2")
    require(data["candidate_scope"] == "internal-l2", "候選範圍不是內部 L2")
    require(data["kernel_family"] == "realtek-rtd129x-bpi", "核心家族不符")
    require(data["current_evidence_level"] == "L2", "目前證據層級不是 L2")
    require(data["target_evidence_level"] == "L2", "目標證據層級不是 L2")
    require(data["source_date_epoch"] == 1571768256, "來源時間戳不符")
    require(
        data["source_contract_projection_sha256"]
        == contract_projection_sha256(data),
        "來源契約投影雜湊不符",
    )
    for key in ("linux_commit", "uboot_commit"):
        require(data[key] == revision, f"{key} 未固定至已審查提交")
    for key in ("linux_ref", "uboot_ref"):
        require(data[key] == f"commit:{revision}", f"{key} 不是精確提交")
    require(data["firmware_commit"] == firmware_revision, "韌體提交不符")
    require(
        data["firmware_ref"] == f"commit:{firmware_revision}",
        "韌體 ref 不是精確提交",
    )
    require(data["verify_firmware_source_resolution"] is True, "完整映像必須核對韌體提交")
    require(data["atf_applicable"] is False, "不得宣稱此路徑建置 TF-A")
    for key in (
        "hardware_validated",
        "public_release_allowed",
        "hardware_claims_allowed",
        "candidate_public_release_approved",
        "hardware_validation_complete",
        "firmware_redistribution_license_verified",
    ):
        require(data[key] is False, f"{key} 必須維持 false")
    if data["full_image_built"]:
        require(data["rootfs_image_built"] is True, "完整映像缺少 rootfs 建置狀態")
        require(data["full_rootfs_image_built"] is True, "完整映像缺少完整 rootfs 證據")
        require(isinstance(data.get("image_build_evidence"), dict), "完整映像缺少機器證據")
        require(status["evidence"]["bananapiw2"]["level"] == "L2", "閉合契約的全域 W2 等級不是 L2")
        validate_image_evidence(data, data["image_build_evidence"])
    else:
        require(data["rootfs_image_built"] is False, "過渡契約不得誤標 rootfs 已建置")
        require(data["full_rootfs_image_built"] is False, "過渡契約不得誤標完整 rootfs")
        require("image_build_evidence" not in data, "過渡契約不得夾帶舊映像證據")
        require(status["evidence"]["bananapiw2"]["level"] == "L1", "過渡契約的全域 W2 等級不是 L1")
        require("bananapiw2" in status["open_findings"], "過渡契約缺少全域 W2 未結項目")
        require(not arguments.verify_historical_image, "過渡契約不能執行歷史映像重驗")
    require(
        data["license_policy"]["opaque_payload_redistribution_verified"] is False,
        "不透明載荷不得誤標已確認授權",
    )
    require(
        data["license_policy"]["toolchain_redistribution_verified"] is False,
        "工具鏈不得誤標已確認授權",
    )
    require(os.environ.get("PUBLIC_RELEASE", "no") != "yes", "未完成授權前禁止公開發布")

    for key in ("linux_license_sha256", "uboot_license_sha256"):
        require(valid_sha256(data[key]), f"{key} 格式不符")
    require(data["linux_license_path"] == "linux-rtk/COPYING", "Linux 授權路徑不符")
    require(
        data["uboot_license_path"] == "u-boot-rtk/Licenses/README",
        "U-Boot 授權路徑不符",
    )

    toolchain = data["build_toolchain"]
    require(toolchain["size"] > 0, "工具鏈 GCC 大小無效")
    require(valid_sha256(toolchain["sha256"]), "工具鏈 GCC 雜湊無效")
    require(valid_sha256(toolchain["manifest_sha256"]), "工具鏈清單雜湊無效")
    require(toolchain["included_in_runtime_image"] is False, "工具鏈不得封裝至執行映像")
    require(
        toolchain["redistribution_license_verified"] is False,
        "工具鏈不得宣稱已確認再散布授權",
    )

    linked_assets = data["linked_prebuilt_assets"]
    expected_linked_assets = {
        "u-boot-rtk/static_lib/libefuse.a",
        "u-boot-rtk/static_lib/libsha1_util.a",
        "u-boot-rtk/static_lib/libsecurity.a",
        "u-boot-rtk/static_lib/libkeyset.a",
    }
    require(set(linked_assets) == expected_linked_assets, "U-Boot 無來源連結資產集合不符")
    for path, asset in linked_assets.items():
        require(asset["size"] > 0, f"{path} 大小無效")
        require(valid_sha256(asset["sha256"]), f"{path} 雜湊無效")
        require(asset["source_build_available"] is False, f"{path} 不得宣稱可重建")
        require(
            asset["redistribution_license_verified"] is False,
            f"{path} 不得宣稱已確認再散布授權",
        )

    runtime_assets = data["runtime_prebuilt_assets"]
    require(len(runtime_assets) == 1, "執行期預建資產數量不符")
    bluecore = runtime_assets[
        "rtk-pack/rtk/bpi-w2/configs/default/linux/bluecore.audio"
    ]
    require(bluecore["size"] == 3969840, "bluecore.audio 大小不符")
    require(valid_sha256(bluecore["sha256"]), "bluecore.audio 雜湊無效")
    require(bluecore["source_build_available"] is False, "bluecore.audio 不得宣稱可重建")
    require(
        bluecore["redistribution_license_verified"] is False,
        "bluecore.audio 不得宣稱已確認再散布授權",
    )

    for evidence in data["documentation_evidence"]:
        require(
            evidence["local_path"].startswith("/media/pi/SMCI/bpi/"),
            "文件證據不在本機受控樹",
        )
        require(valid_sha256(evidence["sha256"]), "文件證據雜湊格式不符")
        require(evidence["included_in_candidate"] is False, "文件證據不得封裝")
        require(
            evidence["redistribution_license_verified"] is False,
            "文件證據不得宣稱已確認再散布授權",
        )

    for expected in (
        'BOARD_MAINTAINER="BPI-SINOVOIP"',
        'ROOT_FS_LABEL="BPI-ROOT"',
        'REALTEK_BPI_ROOT_LABEL="BPI-ROOT"',
        f'REALTEK_BPI_BSP_BRANCH="commit:{revision}"',
        f'ARMBIAN_FIRMWARE_GIT_REF_BOARD="commit:{firmware_revision}"',
        'declare -g IMAGE_PARTITION_TABLE="msdos"',
        "USB_CONFIGFS_MASS_STORAGE",
    ):
        require(expected in board_text, f"板級設定缺少 {expected}")
    require(
        "build_custom_uboot__realtek_bpi_legacy_bsp" in family_text,
        "Realtek 共用家族缺少 vendor U-Boot 建置入口",
    )

    require("REALTEK_BPI_ROOT_LABEL" in family_text, "Realtek 家族缺少根標籤契約")
    require("sed -i -E" in family_text, "Realtek 家族未正規化 U-Boot 根標籤")
    require("SOURCE_DATE_EPOCH" in timestamp_patch, "U-Boot 修補未固定建置時間")
    require("date -u" in timestamp_patch, "U-Boot 修補未固定時區")
    require('model = "Banana Pi BPI-W2";' in identity_patch, "核心修補缺少板級 model")
    require(
        'compatible = "bananapi,bpi-w2", "realtek,rtd1296";' in identity_patch,
        "核心修補缺少板級 compatible",
    )

    board = data["boards"]["bananapiw2"]
    require(
        board["final_kernel_config_sha256"]
        == "0bcd9fdd4e4dcbb1dbe5bd2702ad08171e425c8abf1f9e30e05f6fe4301ec6a3",
        "最終核心設定校準雜湊不符",
    )
    if data["full_image_built"]:
        require(
            board["image_dtb_sha256"] == board["dtb_sha256"],
            "完整映像 DTB 雜湊與板級契約不符",
        )
        require(
            board["dtb_sha256_evidence_scope"] == "full-image-l2",
            "完整映像 DTB 證據範圍不符",
        )
    else:
        require(board["image_dtb_sha256"] is None, "過渡契約不得預填映像 DTB 雜湊")
        require(
            board["dtb_sha256_evidence_scope"] == "component-only-l1",
            "過渡契約 DTB 證據範圍不符",
        )
    require(board["partition_table"] == "msdos", "分割表契約不符")
    require(board["partition_start_sector"] == 8192, "FAT 分割區起點不符")
    require(board["root_partition_start_sector"] == 532480, "根分割區起點不符")
    require(board["uboot_write_offset_bytes"] == 40960, "U-Boot 寫入偏移不符")
    require(board["uboot_offset"] == 40960, "U-Boot 映像偏移不符")
    require(board["uboot_payload"] == "u-boot.bin", "U-Boot 載荷名稱不符")
    require(board["uboot_package_defconfig_required"] is False, "vendor U-Boot 不封裝 defconfig")
    require(board["root_filesystem_label"] == "BPI-ROOT", "根標籤契約不符")
    require(board["root_partition_label"] == "BPI-ROOT", "根分割區標籤不符")
    require(board["boot_partition_label"] == "BPI-BOOT", "開機分割區標籤不符")
    require(board["boot_configuration"] == "realtek_bpi_uenv", "Realtek 開機模式不符")
    require(board["vendor_boot_dtbs"] == ["rtd-1296-bananapi-w2-2GB.dtb"], "vendor DTB 契約不符")
    require(board["usb_contract"]["hardware_role_validated"] is False, "USB 角色不得宣稱實測")
    require(board["display_contract"]["hdmi_tx_status"] == "okay", "HDMI TX 狀態不符")
    require(board["display_contract"]["hdmi_rx_status"] == "disabled", "HDMI RX 狀態不符")
    require(
        board["display_contract"]["displayport_tx_status"] == "okay",
        "DisplayPort TX 狀態不符",
    )
    require(board["display_contract"]["acceleration_validated"] is False, "顯示加速不得宣稱實測")
    require(board["io_contract"]["pin_mapping_hardware_validated"] is False, "腳位不得宣稱實測")
    require(board["wireless_contract"]["onboard_wifi_present"] is False, "W2 不得宣稱板載 Wi-Fi")

    if data["component_build_completed"]:
        evidence = data.get("component_build_evidence")
        require(isinstance(evidence, dict), "元件完成但缺少元件證據")
        require(
            evidence["local_evidence_root"]
            == "output/components/2026.08/bananapi-realtek-rtd1296-w2-legacy",
            "本機元件證據路徑不符",
        )
        require(evidence["source_revision"] == revision, "元件來源提交不符")
        require(evidence["full_rootfs_image_built"] is False, "元件證據不得宣稱完整 rootfs")
        require(evidence["uboot_rebuild_hash_match"] is True, "U-Boot 重建雜湊不一致")
        require(valid_sha256(evidence["uboot_rebuild_sha256"]), "U-Boot 重建雜湊無效")
        require(evidence["work_size_kib"] <= 10 * 1024 * 1024, "元件工作目錄超過上限")
        for name, artifact in evidence["artifacts"].items():
            require(artifact["size"] > 0, f"{name} 大小無效")
            require(valid_sha256(artifact["sha256"]), f"{name} 雜湊無效")
        require(
            evidence["artifacts"]["u-boot.bin"]["sha256"]
            == evidence["uboot_rebuild_sha256"],
            "U-Boot 產物與重建雜湊不符",
        )
        require(
            board["dtb_sha256"]
            == evidence["artifacts"][Path(board["dtb"]).name]["sha256"],
            "DTB 板級雜湊與元件證據不符",
        )

    if arguments.verify_historical_image:
        validate_historical_image(data, data["image_build_evidence"])

    if arguments.print_source_contract_projection_sha256:
        print(contract_projection_sha256(data))
    elif arguments.verify_historical_image:
        print("W2 固定正式 IMG、XZ、清單與原始提交歷史重驗通過。")
    else:
        print("W2 固定來源、二進位資產與發布邊界檢查通過。")


if __name__ == "__main__":
    main()
