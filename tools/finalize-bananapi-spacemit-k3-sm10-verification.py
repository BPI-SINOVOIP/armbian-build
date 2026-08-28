#!/usr/bin/env python3
"""將 SM10 共用映像驗證結果閉合成 L1 校準或 L2 材料證據。"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess


def fail(message: str) -> None:
    raise SystemExit(f"SM10 證據閉合失敗：{message}")


def require(condition: bool, message: str) -> None:
    if not condition:
        fail(message)


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def load_json(path: Path) -> dict:
    require(path.is_file(), f"缺少 JSON：{path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    require(isinstance(value, dict), f"JSON 不是物件：{path}")
    return value


def load_tsv(path: Path) -> list[dict[str, str]]:
    require(path.is_file(), f"缺少 TSV：{path}")
    with path.open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream, delimiter="\t"))
    require(rows and all(None not in row for row in rows), f"TSV 內容不完整：{path}")
    return rows


def atomic_json(path: Path, value: dict) -> None:
    temporary = path.with_name(path.name + ".writing")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--level", choices=("L1", "L2"), required=True)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    repo = arguments.repo.resolve()
    config_path = arguments.config.resolve()
    output = arguments.output.resolve()
    require(config_path.is_relative_to(repo), "驗證契約不在來源倉庫內")
    require(output.is_relative_to(repo), "輸出目錄不在來源倉庫內")
    config = load_json(config_path)
    board = config["boards"]["bananapism10"]
    require(config["current_evidence_level"] == arguments.level, "契約證據層級與閉合模式不同")

    candidates = load_tsv(output / "CANDIDATES.tsv")
    require(len(candidates) == 1 and candidates[0]["board"] == "bananapism10", "候選矩陣身分或筆數不符")
    row = candidates[0]
    board_dir = output / "bananapism10"
    image = (output / row["img_path"]).resolve()
    archive = (output / row["xz_path"]).resolve()
    require(image.parent == board_dir.resolve() and archive.parent == board_dir.resolve(), "產物路徑逸出板級目錄")
    for path, size_field, hash_field in (
        (image, "raw_size", "raw_sha256"),
        (archive, "xz_size", "xz_sha256"),
    ):
        require(path.is_file(), f"缺少產物：{path}")
        require(path.stat().st_size == int(row[size_field]), f"產物大小不符：{path.name}")
        require(digest(path) == row[hash_field], f"產物雜湊不符：{path.name}")

    parsed = subprocess.run(
        ["sfdisk", "--json", str(image)],
        text=True,
        capture_output=True,
        check=False,
    )
    require(parsed.returncode == 0, "無法解析候選 GPT")
    table = json.loads(parsed.stdout)["partitiontable"]
    partitions = table.get("partitions", [])
    require(table.get("label") == "gpt" and len(partitions) == 2, "候選不是雙分割區 GPT")
    normalized_partitions = [
        {
            "number": index,
            "name": partition.get("name", ""),
            "start": int(partition["start"]),
            "size": int(partition["size"]),
            "type": str(partition.get("type", "")).upper(),
            "uuid": str(partition.get("uuid", "")).lower(),
        }
        for index, partition in enumerate(partitions, 1)
    ]
    require(
        [(item["number"], item["name"], item["start"]) for item in normalized_partitions]
        == [(1, "bootfs", 24576), (2, "rootfs", 548864)],
        "候選 GPT 名稱或起點不符",
    )
    require(
        [item["type"] for item in normalized_partitions]
        == [
            "BC13C2FF-59E6-4262-A352-B275FD6F7172",
            "72EC70A6-CF74-40E6-BD49-4BDA08E8F224",
        ],
        "候選 GPT 類型不符",
    )
    require(
        all(re.fullmatch(r"[0-9a-f-]{36}", item["uuid"]) for item in normalized_partitions)
        and normalized_partitions[0]["uuid"] != normalized_partitions[1]["uuid"],
        "候選 GPT PARTUUID 無效或重複",
    )

    final_rows = load_tsv(output / "FINAL_CONFIG_EVIDENCE.tsv")
    require(len(final_rows) == 2, "最終設定清單必須含核心與 U-Boot")
    final_configs = {row["component"]: row for row in final_rows}
    require(set(final_configs) == {"kernel", "uboot"}, "最終設定元件集合不符")
    require(final_configs["uboot"]["sha256"] == board["final_uboot_config_sha256"], "最終 U-Boot 設定不符")
    common_status_path = output / "VERIFICATION_STATUS.json"
    pending_status_path = output / "VERIFICATION_STATUS.json.partial"
    status_path = pending_status_path if arguments.level == "L2" else common_status_path
    status = load_json(status_path)
    require(status.get("status") == "complete" and status.get("evidence_level") == arguments.level, "共用驗證狀態不完整")
    require(status.get("source_commit") == row["source_commit"], "共用驗證與候選提交不同")
    require(status.get("xz_stream_verified") is True, "共用驗證未確認 XZ 串流")
    require(status.get("source_date_epoch") == config["source_date_epoch"], "共用驗證時間基準不符")

    material = {
        "status": "complete",
        "evidence_level": arguments.level,
        "source_commit": status["source_commit"],
        "source_tree": status["source_tree"],
        "verifier_commit": status["verifier_commit"],
        "build_validation_config_sha256": status["build_validation_config_sha256"],
        "verification_config_sha256": status["verification_config_sha256"],
        "source_contract_projection_sha256": status["source_contract_projection_sha256"],
        "source_date_epoch": status["source_date_epoch"],
        "candidate_matrix_sha256": status["candidate_matrix_sha256"],
        "completion_status_sha256": status["completion_status_sha256"],
        "verification_manifest_sha256": digest(output / "VERIFICATION.tsv"),
        "uboot_payload_manifest_sha256": status["uboot_payload_manifest_sha256"],
        "final_config_manifest_sha256": status["final_config_manifest_sha256"],
        "verified_utc": status["verified_utc"],
        "full_rootfs_image_built": True,
        "read_only_content_verified": True,
        "hardware_tested": False,
        "public_release_allowed": False,
        "hardware_claims_allowed": False,
        "hardware_validated": False,
        "xz_stream_verified": True,
        "image": {
            "path": str(image.relative_to(repo)),
            "size": image.stat().st_size,
            "sha256": digest(image),
        },
        "archive": {
            "path": str(archive.relative_to(repo)),
            "size": archive.stat().st_size,
            "sha256": digest(archive),
        },
        "linux_dtb": {
            "path": board["dtb"],
            "sha256": board["dtb_sha256"],
        },
        "gpt": {
            "label": "gpt",
            "logical_sector_size": 512,
            "partitions": normalized_partitions,
        },
        "final_configs": final_configs,
    }
    if arguments.level == "L1":
        require(any("*" in item for item in board["required_partitions"]), "L1 必須保留待校準 GPT 大小")
        require(board["final_kernel_config_sha256"] is None, "L1 不得預填最終核心設定")
        atomic_json(output / "SM10_CALIBRATION.json", material)
        print(f"SM10 L1 校準證據已建立：{output / 'SM10_CALIBRATION.json'}")
        return

    require(all("*" not in item for item in board["required_partitions"]), "L2 不得含 GPT 萬用大小")
    require(final_configs["kernel"]["sha256"] == board["final_kernel_config_sha256"], "L2 最終核心設定不符")
    status.update(
        {
            "verification_manifest_sha256": material["verification_manifest_sha256"],
            "full_rootfs_image_built": True,
            "read_only_content_verified": True,
            "hardware_tested": False,
            "public_release_allowed": False,
            "hardware_claims_allowed": False,
            "hardware_validated": False,
            "image": material["image"],
            "archive": material["archive"],
            "linux_dtb": material["linux_dtb"],
        }
    )
    atomic_json(output / "SM10_MATERIAL_EVIDENCE.json", material)
    atomic_json(common_status_path, status)
    pending_status_path.unlink()
    print(f"SM10 L2 材料證據已閉合：{output / 'SM10_MATERIAL_EVIDENCE.json'}")


if __name__ == "__main__":
    main()
