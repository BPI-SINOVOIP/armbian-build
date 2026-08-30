#!/usr/bin/env python3
"""檢查 Banana Pi SM10 固定來源、完整映像狀態與發布邊界。"""

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
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]
VALIDATION_RELATIVE = "config/validation/bananapi-spacemit-k3-sm10-current.json"
DEFAULT_CONFIG = ROOT / VALIDATION_RELATIVE
IMAGE_OUTPUT_RELATIVE = (
    "output/images/2026.08/bananapi-spacemit-k3-sm10-trixie-current-cli"
)
IMAGE_OUTPUT = ROOT / IMAGE_OUTPUT_RELATIVE
BOARD = ROOT / "config/boards/bananapism10.wip"
FAMILY = ROOT / "config/sources/families/spacemit-k3-bpi.conf"
STATUS = ROOT / "config/bananapi-optimization-status.json"
LINUX_DTS = (
    ROOT
    / "patch/kernel/archive/spacemit-k3-bpi-6.18/dt/k3-bananapi-sm10.dts"
)
BOOT_ENV = ROOT / "packages/blobs/riscv64/spacemit-k3/bpi-sm10/env_k3.txt"
SOURCE_DATE_EPOCH = 1777390324
FIRMWARE_REVISION = "f50a2a21bcdb77a562b3976930c5c6b521a1df08"
L1_CALIBRATED_PARTITIONS = [
    "1:bootfs:24576:524288",
    "2:rootfs:548864:3192832",
]
L1_CALIBRATED_KERNEL_CONFIG_SHA256 = (
    "2ea6c3b62bd8118b685a10d6c4c22a1718df7a9e533c3e929282fcee90c82445"
)
L1_CALIBRATED_IMAGE_DTB_SHA256 = (
    "a74520d979cc62fcdb12dfddd97c7968900109df6a33ae34c1489d87a34695ba"
)
L1_CALIBRATION_EVIDENCE_SHA256 = (
    "c9fadb5272c4052c30189967e3f417ac0195b0869beb9cf52e22ce6d375a4380"
)
CONTRACT_PROJECTION_EXCLUDED_TOP_LEVEL = {
    "candidate_level",
    "candidate_scope",
    "candidate_state",
    "component_build_evidence",
    "current_evidence_level",
    "full_image_built",
    "full_rootfs_image_built",
    "image_build_evidence",
    "l1_calibration_evidence",
    "rootfs_image_built",
    "source_contract_projection_sha256",
}
CONTRACT_PROJECTION_EXCLUDED_BOARD_FIELDS = {
    "dtb_sha256_evidence_scope",
    "final_kernel_config_sha256",
    "image_dtb_sha256",
    "required_partitions",
}


def fail(message: str) -> None:
    raise SystemExit(f"SM10 來源政策拒絕：{message}")


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


def structured_sha256(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def load_json(path: Path, description: str) -> dict[str, object]:
    require(path.is_file(), f"缺少{description}：{path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        fail(f"{description}無法解析：{error}")
    require(isinstance(value, dict), f"{description}不是物件")
    return value


def git_output(*arguments: str) -> bytes:
    result = subprocess.run(
        ["git", "-C", str(ROOT), *arguments],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    require(result.returncode == 0, f"Git 證據不存在：{' '.join(arguments)}")
    return result.stdout


def load_tsv(path: Path, fields: list[str], description: str) -> list[dict[str, str]]:
    require(path.is_file(), f"缺少{description}：{path}")
    try:
        reader = csv.DictReader(
            io.StringIO(path.read_text(encoding="utf-8")), delimiter="\t"
        )
        require(reader.fieldnames == fields, f"{description}欄位不符")
        rows = list(reader)
    except UnicodeError as error:
        fail(f"{description}無法解析：{error}")
    require(rows and all(None not in row for row in rows), f"{description}內容不完整")
    return rows


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


def split_contract(specifications: list[str], delimiter: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for specification in specifications:
        require(delimiter in specification, f"契約格式不符：{specification}")
        name, value = specification.split(delimiter, 1)
        require(name and value and name not in values, f"契約名稱空白或重複：{name}")
        values[name] = value
    return values


def validate_image_evidence(config: dict[str, object], evidence: dict[str, object]) -> None:
    require(evidence.get("status") == "complete", "L2 映像證據尚未完成")
    require(evidence.get("evidence_level") == "L2", "L2 映像證據層級不符")
    require(evidence.get("full_rootfs_image_built") is True, "L2 缺少完整 rootfs 證據")
    require(evidence.get("read_only_content_verified") is True, "L2 缺少唯讀內容驗證")
    require(evidence.get("hardware_tested") is False, "L2 不得冒充實機驗證")
    require(evidence.get("source_date_epoch") == SOURCE_DATE_EPOCH, "L2 時間基準不符")
    require(evidence.get("xz_stream_verified") is True, "L2 缺少 XZ 串流驗證")
    for field in ("source_commit", "source_tree", "verifier_commit"):
        require(
            isinstance(evidence.get(field), str)
            and re.fullmatch(r"[0-9a-f]{40}", evidence[field]) is not None,
            f"L2 {field} 格式不符",
        )
    require(evidence["source_commit"] == evidence["verifier_commit"], "L2 建置與驗證提交不同")
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
        "L2 建置與驗證契約雜湊不同",
    )
    source_commit = evidence["source_commit"]
    git_output("cat-file", "-e", f"{source_commit}^{{commit}}")
    ancestry = subprocess.run(
        ["git", "-C", str(ROOT), "merge-base", "--is-ancestor", source_commit, "HEAD"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    require(ancestry.returncode == 0, "L2 來源提交不是目前分支祖先")
    source_tree = git_output("rev-parse", f"{source_commit}^{{tree}}").decode().strip()
    require(evidence["source_tree"] == source_tree, "L2 來源 tree 不符")
    validation_blob = git_output("show", f"{source_commit}:{VALIDATION_RELATIVE}")
    require(
        evidence["build_validation_config_sha256"]
        == hashlib.sha256(validation_blob).hexdigest(),
        "L2 建置契約未綁定來源提交",
    )
    try:
        source_config = json.loads(validation_blob.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        fail(f"L2 來源契約無法解析：{error}")
    projection = contract_projection_sha256(config)
    require(contract_projection_sha256(source_config) == projection, "L2 來源契約投影已改變")
    require(
        evidence["source_contract_projection_sha256"] == projection,
        "L2 證據未綁定來源契約投影",
    )
    board = config["boards"]["bananapism10"]
    linux_dtb = evidence.get("linux_dtb")
    require(isinstance(linux_dtb, dict), "L2 缺少映像 DTB 證據")
    require(linux_dtb.get("path") == board["dtb"], "L2 映像 DTB 路徑不符")
    require(
        linux_dtb.get("sha256") == board["image_dtb_sha256"],
        "L2 映像 DTB 雜湊不符",
    )
    for name, suffix in (("image", ".img"), ("archive", ".img.xz")):
        artifact = evidence.get(name)
        require(isinstance(artifact, dict), f"L2 {name} 證據格式不符")
        relative = artifact.get("path")
        require(
            isinstance(relative, str)
            and relative.startswith(f"{IMAGE_OUTPUT_RELATIVE}/bananapism10/")
            and relative.endswith(suffix)
            and ".." not in Path(relative).parts,
            f"L2 {name} 路徑不合法",
        )
        require(isinstance(artifact.get("size"), int) and artifact["size"] > 0, f"L2 {name} 大小無效")
        require(valid_sha256(artifact.get("sha256")), f"L2 {name} 雜湊格式不符")


def validate_historical_image(config: dict[str, object], evidence: dict[str, object]) -> None:
    require(IMAGE_OUTPUT.is_dir(), f"缺少 SM10 固定正式輸出：{IMAGE_OUTPUT}")
    matrix = IMAGE_OUTPUT / "CANDIDATES.tsv"
    completion = IMAGE_OUTPUT / "COMPLETION_STATUS.json"
    verification = IMAGE_OUTPUT / "VERIFICATION.tsv"
    verification_status = IMAGE_OUTPUT / "VERIFICATION_STATUS.json"
    payload_manifest = IMAGE_OUTPUT / "UBOOT_PAYLOAD_EVIDENCE.tsv"
    config_manifest = IMAGE_OUTPUT / "FINAL_CONFIG_EVIDENCE.tsv"
    rows = load_tsv(
        matrix,
        ["board", "release", "profile", "raw_size", "raw_sha256", "xz_size", "xz_sha256", "img_path", "xz_path", "source_commit", "uboot_tag"],
        "SM10 候選矩陣",
    )
    require(len(rows) == 1, "SM10 候選矩陣必須只有一筆")
    row = rows[0]
    require(
        (row["board"], row["release"], row["profile"], row["uboot_tag"])
        == ("bananapism10", "trixie", "cli", "v2022.10"),
        "SM10 候選矩陣身分不符",
    )
    require(row["source_commit"] == evidence["source_commit"], "SM10 候選來源提交不符")
    require(digest(matrix) == evidence["candidate_matrix_sha256"], "SM10 候選矩陣雜湊不符")
    image = (IMAGE_OUTPUT / row["img_path"]).resolve()
    archive = (IMAGE_OUTPUT / row["xz_path"]).resolve()
    expected_parent = (IMAGE_OUTPUT / "bananapism10").resolve()
    require(image.parent == expected_parent and archive.parent == expected_parent, "SM10 產物路徑逸出固定目錄")
    require((ROOT / evidence["image"]["path"]).resolve() == image, "SM10 IMG 證據路徑不符")
    require((ROOT / evidence["archive"]["path"]).resolve() == archive, "SM10 XZ 證據路徑不符")
    require(
        evidence["image"]["size"] == int(row["raw_size"])
        and evidence["image"]["sha256"] == row["raw_sha256"],
        "SM10 IMG 證據與候選矩陣不符",
    )
    require(archive.is_file(), "缺少 SM10 XZ")
    require(
        archive.stat().st_size == evidence["archive"]["size"] == int(row["xz_size"]),
        "SM10 XZ 大小不符",
    )
    require(
        digest(archive) == evidence["archive"]["sha256"] == row["xz_sha256"],
        "SM10 XZ 雜湊不符",
    )
    xz_test = subprocess.run(["xz", "-t", "--", str(archive)], capture_output=True, check=False)
    require(xz_test.returncode == 0, "SM10 XZ 結構或校驗碼不符")
    board = config["boards"]["bananapism10"]
    sizes = split_contract(board["uboot_payload_sizes"], "=")
    hashes = split_contract(board["uboot_payload_sha256"], "=")
    offsets = split_contract(board["uboot_payloads"], "@")
    with tempfile.NamedTemporaryFile(prefix="sm10-history-", suffix=".img") as temporary_image:
        decompressed = subprocess.run(
            ["xz", "-dc", "--", str(archive)],
            stdout=temporary_image,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        require(decompressed.returncode == 0, "SM10 XZ 無法完整解壓")
        temporary_image.flush()
        temporary_path = Path(temporary_image.name)
        require(
            temporary_path.stat().st_size == evidence["image"]["size"]
            and digest(temporary_path) == evidence["image"]["sha256"],
            "SM10 XZ 解壓內容與 IMG 證據不同",
        )
        require(
            subprocess.run(
                ["sgdisk", "-v", str(temporary_path)],
                capture_output=True,
                check=False,
            ).returncode
            == 0,
            "SM10 GPT 結構或 CRC 不完整",
        )
        parsed = subprocess.run(
            ["sfdisk", "--json", str(temporary_path)],
            capture_output=True,
            text=True,
            check=False,
        )
        require(parsed.returncode == 0, "SM10 GPT 無法重新解析")
        table = json.loads(parsed.stdout)["partitiontable"]
        require(table.get("label") == "gpt", "SM10 解壓映像不是 GPT")
        partitions = table.get("partitions", [])
        specifications = board["required_partitions"]
        types = board["required_partition_types"]
        require(len(partitions) == len(specifications) == len(types) == 2, "SM10 GPT 分割區數量不符")
        for index, (specification, type_specification) in enumerate(zip(specifications, types)):
            number, name, start, size = specification.split(":", 3)
            type_number, expected_type = type_specification.split(":", 1)
            require("*" not in specification, "L2 GPT 契約不得含萬用值")
            require(number == type_number == str(index + 1), "SM10 GPT 契約編號不連續")
            partition = partitions[index]
            require(
                (partition.get("name", ""), str(partition.get("start", "")), str(partition.get("size", "")))
                == (name, start, size),
                f"SM10 GPT 第 {number} 分割區不符",
            )
            require(str(partition.get("type", "")).lower() == expected_type.lower(), f"SM10 GPT 第 {number} 類型不符")
        with temporary_path.open("rb") as stream:
            for name, offset in offsets.items():
                stream.seek(int(offset))
                payload = stream.read(int(sizes[name]))
                require(hashlib.sha256(payload).hexdigest() == hashes[name], f"SM10 IMG 內載荷不符：{name}")

    completion_data = load_json(completion, "SM10 建置完成狀態")
    verification_data = load_json(verification_status, "SM10 驗證完成狀態")
    require(completion_data.get("status") == verification_data.get("status") == "complete", "SM10 建置或驗證狀態未完成")
    require(digest(completion) == evidence["completion_status_sha256"], "SM10 建置狀態雜湊不符")
    protected_mapping = {
        "source_commit": "source_commit",
        "source_tree": "source_tree",
        "build_validation_config_sha256": "validation_config_sha256",
        "candidate_matrix_sha256": "candidates_sha256",
        "source_contract_projection_sha256": "source_contract_projection_sha256",
        "source_date_epoch": "source_date_epoch",
    }
    for evidence_field, completion_field in protected_mapping.items():
        require(completion_data.get(completion_field) == evidence[evidence_field], f"SM10 建置狀態 {completion_field} 不符")
    for field in (
        "source_commit", "source_tree", "verifier_commit", "build_validation_config_sha256",
        "verification_config_sha256", "source_contract_projection_sha256", "candidate_matrix_sha256",
        "completion_status_sha256", "source_date_epoch", "uboot_payload_manifest_sha256",
        "final_config_manifest_sha256",
    ):
        require(verification_data.get(field) == evidence[field], f"SM10 驗證狀態 {field} 不符")
    require(verification_data.get("evidence_level") == "L2", "SM10 驗證狀態不是 L2")
    require(verification_data.get("xz_stream_verified") is True, "SM10 驗證狀態未確認 XZ")
    require(verification_data.get("verified_utc") == evidence["verified_utc"], "SM10 驗證時間不符")
    for field in ("public_release_allowed", "hardware_claims_allowed", "hardware_validated"):
        require(verification_data.get(field) is False, f"SM10 驗證狀態不得把 {field} 標為 true")

    verification_rows = load_tsv(verification, ["board", "identity", "read_only_content", "evidence_level"], "SM10 驗證清單")
    require(verification_rows == [{"board": "bananapism10", "identity": "pass", "read_only_content": "pass", "evidence_level": "L2"}], "SM10 驗證清單內容不符")
    require(digest(verification) == evidence["verification_manifest_sha256"], "SM10 驗證清單雜湊不符")
    require(digest(payload_manifest) == evidence["uboot_payload_manifest_sha256"], "SM10 U-Boot 載荷清單雜湊不符")
    require(digest(config_manifest) == evidence["final_config_manifest_sha256"], "SM10 最終設定清單雜湊不符")

    expected_payloads = []
    for name, offset in offsets.items():
        expected_payloads.append({"board": "bananapism10", "payload": name, "placement": "image", "offset": offset, "size": sizes[name], "sha256": hashes[name]})
    for name in board["uboot_package_only_payloads"]:
        expected_payloads.append({"board": "bananapism10", "payload": name, "placement": "package-only", "offset": "-", "size": sizes[name], "sha256": hashes[name]})
    payload_rows = load_tsv(payload_manifest, ["board", "payload", "placement", "offset", "size", "sha256"], "SM10 U-Boot 載荷清單")
    require(payload_rows == expected_payloads, "SM10 U-Boot 載荷清單內容不符")
    config_rows = load_tsv(config_manifest, ["board", "component", "path", "sha256"], "SM10 最終設定清單")
    require(len(config_rows) == 2, "SM10 最終設定清單必須含核心與 U-Boot")
    by_component = {row["component"]: row for row in config_rows}
    require(by_component.get("kernel", {}).get("sha256") == board["final_kernel_config_sha256"], "SM10 最終核心設定不符")
    require(by_component.get("uboot", {}).get("sha256") == board["final_uboot_config_sha256"], "SM10 最終 U-Boot 設定不符")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("契約", nargs="?", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--print-source-contract-projection-sha256", action="store_true", help="輸出排除校準與完成狀態後的來源契約投影雜湊")
    parser.add_argument("--verify-historical-image", action="store_true", help="重新核對版本控制內 L2 證據與正式 IMG、XZ 及清單")
    arguments = parser.parse_args()
    config_path = arguments.契約.resolve()
    require(not arguments.verify_historical_image or config_path == DEFAULT_CONFIG.resolve(), "歷史重驗只接受固定 SM10 契約")
    require(not (arguments.verify_historical_image and arguments.print_source_contract_projection_sha256), "歷史重驗不得只輸出契約投影")
    config = load_json(config_path, "SM10 驗證契約")
    if arguments.print_source_contract_projection_sha256:
        print(contract_projection_sha256(config))
        return

    status = load_json(STATUS, "全域最佳化狀態")
    board_text = BOARD.read_text(encoding="utf-8")
    family_text = FAMILY.read_text(encoding="utf-8")
    dts_text = LINUX_DTS.read_text(encoding="utf-8")
    env_text = BOOT_ENV.read_text(encoding="utf-8")
    board = config["boards"]["bananapism10"]
    revisions = {
        "manifest": "6d767b42fdbd759dc9511b8a13523c3de42aaa5a",
        "linux": "27275ec8240cc49af3a525b8bc325d9b5029fb81",
        "uboot": "1b10c8119e1a9b5451a4236f6b384f7c91eed1e2",
        "opensbi": "3e2f9efc9660b8d5fcae4e0b6495f306d5c64078",
        "esos": "92a8baf250e42853a094a7af6f7ee849adb3de4a",
    }
    require(config["schema_version"] == 1, "驗證契約版本不符")
    require(config["candidate_branch"] == "current", "候選分支不是 current")
    require(config["kernel_family"] == "spacemit-k3-bpi", "核心家族不符")
    require(config["source_date_epoch"] == SOURCE_DATE_EPOCH, "來源時間基準不符")
    require(config["source_contract_projection_sha256"] == contract_projection_sha256(config), "來源契約投影雜湊不符")
    require(config["linux_commit"] == revisions["linux"], "Linux 提交不符")
    require(config["linux_ref"] == f"commit:{revisions['linux']}", "Linux ref 不精確")
    require(config["firmware_commit"] == FIRMWARE_REVISION, "Armbian 韌體提交不符")
    require(config["firmware_ref"] == f"commit:{FIRMWARE_REVISION}", "Armbian 韌體 ref 不精確")
    require(config["verify_firmware_source_resolution"] is True, "完整映像必須核對韌體來源")
    require(config["sdk"]["manifest_commit"] == revisions["manifest"], "manifest 提交不符")
    require(config["sdk"]["project_count"] == len(config["source_commits"]) == 20, "SDK 來源未完整固定 20 個專案")
    for path, revision in config["source_commits"].items():
        require(bool(path) and re.fullmatch(r"[0-9a-f]{40}", revision) is not None, f"來源提交格式不符：{path}")
    for name in ("linux", "uboot", "opensbi", "esos"):
        source = config["component_sources"][name]
        require(source["revision"] == revisions[name], f"{name} revision 不符")
        require(source["ref"] == f"commit:{revisions[name]}", f"{name} ref 不精確")
        require(source["revision"] in config["source_commits"].values(), f"{name} 不在 manifest")
    require(config["trusted_firmware_a"]["applicable"] is False, "RISC-V K3 不得宣稱使用 TF-A")
    for field in (
        "public_release_allowed", "public_distribution_approved", "hardware_claims_allowed",
        "hardware_validated", "hardware_validation_complete", "candidate_public_release_approved",
        "secure_boot_claim_allowed",
    ):
        require(config[field] is False, f"{field} 必須維持 false")
    require(os.environ.get("PUBLIC_RELEASE", "no") != "yes", "授權未閉合前禁止公開發布")
    require(config["candidate_boot_media"] == ["sd"] and config["supported_boot_media"] == [], "不得把 SD 設計目標誤標為實機支援")
    require(len(config["public_distribution_blockers"]) >= 6, "公開散布阻擋記錄不足")
    require(len(config["private_signing_keys_in_sdk"]) >= 6, "SDK 私鑰風險記錄不足")
    for field in (
        "component_build_completed",
        "full_image_built",
        "rootfs_image_built",
        "full_rootfs_image_built",
    ):
        require(type(config.get(field)) is bool, f"{field} 必須是布林值")

    current_level = config["current_evidence_level"]
    require(config["target_evidence_level"] == "L2", "目標證據層級不是 L2")
    if current_level == "L2":
        calibration_evidence = config.get("l1_calibration_evidence")
        require(isinstance(calibration_evidence, dict), "L2 缺少 L1 校準機器證據")
        require(
            structured_sha256(calibration_evidence)
            == L1_CALIBRATION_EVIDENCE_SHA256,
            "L1 校準機器證據雜湊不符",
        )
        require(
            calibration_evidence["source_contract_projection_sha256"]
            == config["source_contract_projection_sha256"],
            "L1 校準與目前來源契約投影不同",
        )
    if config["full_image_built"] is True:
        require(current_level == "L2", "完整映像狀態必須是 L2")
        require(config["candidate_level"] == "L2 內部軟體候選", "L2 候選名稱不符")
        require(config["candidate_scope"] == "internal-l2", "L2 候選範圍不符")
        require(config["candidate_state"] == "l2-closed", "L2 完成狀態不符")
        require(config["rootfs_image_built"] is True and config["full_rootfs_image_built"] is True, "L2 缺少完整 rootfs 狀態")
        require(isinstance(config.get("image_build_evidence"), dict), "L2 缺少映像機器證據")
        require(status["evidence"]["bananapism10"]["level"] == "L2", "全域 SM10 等級不是 L2")
        validate_image_evidence(config, config["image_build_evidence"])
    elif current_level == "L2":
        require(config["candidate_level"] == "L2 內部軟體候選", "L2 過渡候選名稱不符")
        require(config["candidate_scope"] == "internal-l2", "L2 過渡候選範圍不符")
        require(config["candidate_state"] == "l2-transition", "L2 過渡狀態不符")
        require(config["rootfs_image_built"] is False and config["full_rootfs_image_built"] is False, "L2 過渡契約不得預填 rootfs 完成")
        require("image_build_evidence" not in config, "L2 過渡契約不得夾帶舊映像證據")
        require(status["evidence"]["bananapism10"]["level"] == "L1", "L2 過渡期間全域 SM10 必須維持 L1")
        require("bananapism10" in status["open_findings"], "L2 過渡契約缺少全域未結項目")
        require(not arguments.verify_historical_image, "L2 過渡契約不能執行歷史映像重驗")
    else:
        require(current_level == "L1", "校準契約必須是 L1")
        require(config["candidate_level"] == "L1 完整映像校準候選", "L1 校準候選名稱不符")
        require(config["candidate_scope"] == "internal-l1-calibration", "L1 校準範圍不符")
        require(config["candidate_state"] == "l1-calibration", "L1 校準狀態不符")
        require("l1_calibration_evidence" not in config, "L1 不得預填校準完成證據")
        require(config["rootfs_image_built"] is False and config["full_rootfs_image_built"] is False, "校準契約不得誤標 rootfs 完成")
        require("image_build_evidence" not in config, "校準契約不得夾帶舊映像證據")
        require(status["evidence"]["bananapism10"]["level"] == "L1", "全域 SM10 等級不是 L1")
        require("bananapism10" in status["open_findings"], "校準契約缺少全域未結項目")
        require(not arguments.verify_historical_image, "L1 校準契約不能執行歷史映像重驗")

    require(BOARD.is_file() and BOARD.suffix == ".wip", "板級設定必須維持 .wip")
    for revision in revisions.values():
        require(revision in board_text, f"板檔缺少固定提交：{revision}")
    require(FIRMWARE_REVISION in board_text, "板檔缺少固定 Armbian 韌體提交")
    for expected in (
        'ARMBIAN_FIRMWARE_GIT_SOURCE_BOARD="https://github.com/armbian/firmware"',
        f'ARMBIAN_FIRMWARE_GIT_REF_BOARD="commit:{FIRMWARE_REVISION}"',
        'declare -g ARMBIAN_FIRMWARE_GIT_SOURCE="${ARMBIAN_FIRMWARE_GIT_SOURCE_BOARD}"',
        'declare -g ARMBIAN_FIRMWARE_GIT_REF="${ARMBIAN_FIRMWARE_GIT_REF_BOARD}"',
    ):
        require(expected in board_text, f"板檔缺少 Armbian 韌體實際固定設定：{expected}")
    require(revisions["linux"] in family_text and revisions["uboot"] in family_text, "family 未固定核心來源")
    for expected in (
        "[FSBL.bin]=\"9a40d9d27ec8de79a38ece8ad00de96d29d45b507c43f46f3bf45589c50034d7\"",
        "[fw_dynamic.itb]=\"37dcca0ad696c88900c316a5bab289f1e3e55f09836cb22a4f09c1faa93be86d\"",
        "[u-boot.itb]=\"f7560b4afd523b484b7f950f038485dea7c28cbf5f9c225290d940ca4461ae13\"",
        "cp -fv uboot.config .config",
        "deploy_spacemit_k3_uboot_target_with_evidence",
        "u-boot-config-target-${uboot_target_counter}",
        "u-boot-metadata-target-${uboot_target_counter}.sh",
        "expected_bmp_sha256=\"a3567f599894c570d1b62c461d52e227b29eca9ab745ac619553890ecf9c2e8b\"",
    ):
        require(expected in family_text, f"family 缺少受控建置內容：{expected}")
    require('#include "k3_com260.dts"' in dts_text, "SM10 DTS 未保守繼承 donor")
    require('model = "BananaPi BPI-SM10";' in dts_text, "SM10 DTS model 不符")
    require('compatible = "bananapi,bpi-sm10", "spacemit,k3-com260";' in dts_text, "SM10 DTS compatible 不符")
    require(board["topology_equivalence_verified"] is False, "不得把 donor 拓撲標成已驗證")
    require(board["uboot_control_dtb_identity_is_bananapi_specific"] is False, "不得把 U-Boot donor DT 標成專屬")

    source_assets = config["source_built_boot_assets"]
    require(len(source_assets) == 7, "來源建置啟動資產數量不符")
    for relative, asset in source_assets.items():
        path = ROOT / relative
        require(path.is_file(), f"缺少來源建置資產：{relative}")
        require(asset["reproducible_rebuild_count"] >= 2, f"來源建置資產缺少兩次重建：{relative}")
        require(path.stat().st_size == asset["size"] and digest(path) == asset["sha256"], f"來源建置資產不符：{relative}")
    env_asset = source_assets["packages/blobs/riscv64/spacemit-k3/bpi-sm10/env.bin"]
    default_env_asset = source_assets[
        "packages/blobs/riscv64/spacemit-k3/bpi-sm10/u-boot-env-default.bin"
    ]
    require(env_asset.get("derived_from") == "u-boot-env-default.bin", "env.bin 缺少來源衍生關係")
    require(env_asset["size"] == default_env_asset["size"] and env_asset["sha256"] == default_env_asset["sha256"], "env.bin 與 U-Boot 預設環境位元組不同")
    runtime_assets = config["runtime_prebuilt_assets"]
    require(len(runtime_assets) == 3, "受控預建資產數量不符")
    for relative, asset in runtime_assets.items():
        path = ROOT / relative
        require(path.is_file(), f"缺少受控預建資產：{relative}")
        require(path.stat().st_size == asset["size"] and digest(path) == asset["sha256"], f"受控預建資產不符：{relative}")
        require(asset["source_build_available"] is False, f"預建資產不得宣稱可重建：{relative}")
        require(asset["redistribution_license_verified"] is False, f"預建資產不得宣稱再散布授權已確認：{relative}")
    require(set(config["bootloader_blobs"]) == {str(path.relative_to(ROOT)) for path in (ROOT / "packages/blobs/riscv64/spacemit-k3/bpi-sm10").iterdir() if path.is_file()}, "啟動資產清冊不是目錄完整集合")
    for relative, expected in config["bootloader_blobs"].items():
        path = ROOT / relative
        require(path.is_file() and digest(path) == expected, f"受控檔案雜湊不符：{relative}")

    require(board["partition_table"] == "gpt", "分割表不是 GPT")
    require(board["partition_start_sector"] == board["boot_partition_start_sector"] == 24576, "bootfs 起點不符")
    require(board["root_partition_start_sector"] == 548864, "rootfs 起點不符")
    require(board["required_partition_types"] == ["1:BC13C2FF-59E6-4262-A352-B275FD6F7172", "2:72EC70A6-CF74-40E6-BD49-4BDA08E8F224"], "GPT 類型契約不符")
    if current_level == "L2":
        require(
            board["required_partitions"] == L1_CALIBRATED_PARTITIONS,
            "L2 GPT 契約與 L1 校準證據不符",
        )
        require(
            board["final_kernel_config_sha256"]
            == L1_CALIBRATED_KERNEL_CONFIG_SHA256,
            "L2 最終核心設定與 L1 校準證據不符",
        )
        require(
            board["image_dtb_sha256"]
            == board["dtb_sha256"]
            == L1_CALIBRATED_IMAGE_DTB_SHA256,
            "L2 映像 DTB 與 L1 校準證據不符",
        )
        expected_scope = (
            "full-image-l2"
            if config["full_image_built"] is True
            else "l1-calibration-image"
        )
        require(
            board["dtb_sha256_evidence_scope"] == expected_scope,
            "L2 DTB 證據範圍不符",
        )
    else:
        require(board["required_partitions"] == ["1:bootfs:24576:*", "2:rootfs:548864:*"], "L1 GPT 校準契約不符")
        require(board["final_kernel_config_sha256"] is None, "L1 不得預填最終核心設定")
        require(board["image_dtb_sha256"] is None, "L1 不得預填映像 DTB")
        require(board["dtb_sha256_evidence_scope"] == "component-only-l1", "L1 DTB 證據範圍不符")
    require(board["final_uboot_config_sha256"] == "ffb244d91c6d9ce59f20eeabee15f0391e5d6417548856cacd4720d87cf69b9c", "最終 U-Boot 設定雜湊不符")
    require(board["boot_configuration"] == "env_k3", "SM10 開機設定不是 env_k3")
    require(board["boot_partition_label"] == "BPI-BOOT" and board["root_partition_label"] == "BPI-ROOT", "分割區標籤契約不符")
    require(digest(BOOT_ENV) == board["env_k3_source_sha256"], "env_k3 來源雜湊不符")
    for key, value in board["boot_environment"].items():
        require(f"{key}={value}\n" in env_text, f"env_k3 欄位不符：{key}")
    sizes = split_contract(board["uboot_payload_sizes"], "=")
    hashes = split_contract(board["uboot_payload_sha256"], "=")
    payloads = split_contract(board["uboot_payloads"], "@")
    package_only = set(board["uboot_package_only_payloads"])
    require(set(sizes) == set(hashes) == set(payloads) | package_only, "U-Boot 載荷大小、雜湊與位置集合不符")
    for name, expected in hashes.items():
        relative = f"packages/blobs/riscv64/spacemit-k3/bpi-sm10/{name}"
        require(config["bootloader_blobs"][relative] == expected, f"U-Boot 載荷與資產雜湊不同：{name}")
        require((ROOT / relative).stat().st_size == int(sizes[name]), f"U-Boot 載荷大小不符：{name}")

    if config["component_build_completed"]:
        component_evidence = config.get("component_build_evidence")
        require(isinstance(component_evidence, dict), "元件完成但缺少元件證據")
        require(component_evidence.get("full_rootfs_image_built") is not True, "元件證據不得冒充完整映像")
        for name, artifact in component_evidence["artifacts"].items():
            require(artifact["size"] > 0 and valid_sha256(artifact["sha256"]), f"元件證據無效：{name}")
        require(component_evidence["artifacts"]["k3-bananapi-sm10.dtb"]["sha256"] == board["dtb_sha256"], "元件 DTB 雜湊不符")

    if arguments.verify_historical_image:
        validate_historical_image(config, config["image_build_evidence"])
        print("SM10 固定正式 IMG、XZ、清單與原始提交歷史重驗通過。")
    else:
        print("SM10 固定來源、二進位資產、映像狀態與發布邊界檢查通過。")


if __name__ == "__main__":
    main()
