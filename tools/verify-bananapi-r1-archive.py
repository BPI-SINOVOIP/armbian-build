#!/usr/bin/env python3
"""以唯讀方式核驗 Banana Pi R1 歷史映像證據。"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, BinaryIO, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONTRACT = (
    REPO_ROOT / "config/validation/bananapi-sunxi-a20-r1-archive.json"
)
ASSIGNMENT_PATTERN = re.compile(r'^([A-Z][A-Z0-9_]*)="([^"]*)"$', re.MULTILINE)
DRAM_PATTERN = re.compile(r'CONFIG_DRAM_CLK\s+"([0-9]+)"')
SIDECAR_PATTERN = re.compile(r"^([0-9a-f]{64})\s+(.+)$")


class VerificationError(RuntimeError):
    """表示證據不符合受控契約。"""


def fail(message: str) -> None:
    raise VerificationError(message)


def run_checked(
    command: Sequence[str],
    *,
    cwd: Path | None = None,
    stdout: BinaryIO | int | None = subprocess.PIPE,
) -> subprocess.CompletedProcess[Any]:
    result = subprocess.run(
        list(command),
        cwd=cwd,
        stdout=stdout,
        stderr=subprocess.PIPE,
        check=False,
        text=stdout == subprocess.PIPE,
    )
    if result.returncode != 0:
        stderr = result.stderr
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        detail = (stderr or "").strip()
        fail(f"命令失敗（{result.returncode}）：{' '.join(command)}：{detail}")
    return result


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_contract(path: Path) -> dict[str, Any]:
    try:
        contract = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        fail(f"無法讀取驗證契約 {path}：{error}")
    if contract.get("schema_version") != 1:
        fail("驗證契約版本不是 1")
    if contract.get("evidence_class") != "L2（歷史／封存）":
        fail("證據分類不是 L2（歷史／封存）")
    claims = contract.get("claims", {})
    expected_claims = {
        "new_build_completed",
        "currently_supported",
        "hardware_tested",
        "public_release_allowed",
    }
    if set(claims) != expected_claims or set(claims.values()) != {False}:
        fail("歷史證據契約不得允許建置、支援、實機或公開發布聲明")
    archives = contract.get("archives", [])
    if len(archives) != 10:
        fail("歷史映像契約必須恰好包含十組資料")
    return contract


def parse_board_contract(text: str) -> dict[str, str]:
    values = dict(ASSIGNMENT_PATTERN.findall(text))
    dram_match = DRAM_PATTERN.search(text)
    if not dram_match:
        fail("板檔缺少 CONFIG_DRAM_CLK 設定")
    values["CONFIG_DRAM_CLK"] = dram_match.group(1)
    return values


def verify_board_equivalence(
    contract: dict[str, Any], repo_root: Path
) -> dict[str, str]:
    board = contract["board"]
    current_path = repo_root / board["current_file"]
    if current_path.suffix != board["required_status_suffix"]:
        fail(f"現行 R1 板檔不是 EOS：{current_path}")
    if current_path.name != f'{board["current_id"]}{board["required_status_suffix"]}':
        fail("現行 R1 板檔名稱與契約不符")
    if not current_path.is_file():
        fail(f"找不到現行 R1 板檔：{current_path}")

    legacy_object = f'{board["legacy_commit"]}:{board["legacy_file"]}'
    legacy_result = run_checked(
        ["git", "show", legacy_object], cwd=repo_root
    )
    current_values = parse_board_contract(current_path.read_text(encoding="utf-8"))
    legacy_values = parse_board_contract(legacy_result.stdout)
    expected = board["equivalent_fields"]
    for field, expected_value in expected.items():
        current_value = current_values.get(field)
        legacy_value = legacy_values.get(field)
        if current_value != expected_value:
            fail(
                f"現行板檔欄位 {field} 不符：預期 {expected_value}，"
                f"實際 {current_value}"
            )
        if legacy_value != expected_value:
            fail(
                f"舊板檔欄位 {field} 不符：預期 {expected_value}，"
                f"實際 {legacy_value}"
            )
    return current_values


def verify_file(path: Path, expected_size: int, expected_sha256: str) -> None:
    if not path.is_file():
        fail(f"找不到證據檔：{path}")
    actual_size = path.stat().st_size
    if actual_size != expected_size:
        fail(f"檔案大小不符：{path}：預期 {expected_size}，實際 {actual_size}")
    actual_sha256 = sha256_file(path)
    if actual_sha256 != expected_sha256:
        fail(
            f"SHA-256 不符：{path}：預期 {expected_sha256}，"
            f"實際 {actual_sha256}"
        )


def parse_metadata(path: Path) -> dict[str, str]:
    fields: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8", errors="strict").splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        if key.strip() and value.strip():
            fields.setdefault(key.strip(), value.strip())
    return fields


def verify_sidecar(path: Path, image_name: str, expected_digest: str) -> None:
    lines = path.read_text(encoding="utf-8", errors="strict").splitlines()
    if len(lines) != 1:
        fail(f"旁車雜湊不是單行格式：{path}")
    match = SIDECAR_PATTERN.fullmatch(lines[0])
    if not match:
        fail(f"旁車雜湊格式無法解析：{path}")
    digest, recorded_path = match.groups()
    if digest != expected_digest:
        fail(f"旁車記錄的 SHA-256 不符：{path}")
    if Path(recorded_path).name != image_name:
        fail(f"旁車記錄的映像名稱不符：{path}")


def xz_uncompressed_size(path: Path) -> int:
    result = run_checked(["xz", "--robot", "-l", "--", str(path)])
    file_rows = [
        line.split("\t")
        for line in result.stdout.splitlines()
        if line.startswith("file\t")
    ]
    if len(file_rows) != 1 or len(file_rows[0]) < 5:
        fail(f"無法解析 XZ 清單：{path}")
    return int(file_rows[0][4])


def required_log_markers(record: dict[str, Any]) -> list[str]:
    raw_image = record["image"][:-3]
    markers = [
        raw_image,
        "SHA256 calculating",
        "Done building image",
        "BOARD=lamobo-r1",
        "BRANCH=current",
        f'RELEASE={record["release"]}',
        "BUILD_MINIMAL=no",
        "Docker run finished",
        "successful",
    ]
    if record["profile"] == "cli":
        markers.append("BUILD_DESKTOP=no")
    else:
        markers.extend(("BUILD_DESKTOP=yes", "DESKTOP_ENVIRONMENT=xfce"))
    return markers


def verify_archive_matrix(
    contract: dict[str, Any], image_root: Path, log_root: Path
) -> None:
    expected_image_files: set[str] = set()
    expected_logs: set[str] = set()
    metadata_contract = contract["metadata"]
    matrix = {(item["release"], item["profile"]) for item in contract["archives"]}
    expected_matrix = {
        (release, profile)
        for release in ("bookworm", "jammy", "noble", "resolute", "trixie")
        for profile in ("cli", "xfce")
    }
    if matrix != expected_matrix:
        fail("歷史映像矩陣不是五個發行版各含 CLI 與 XFCE")

    for record in contract["archives"]:
        expected_image_files.update(
            (record["image"], record["sidecar"], record["metadata_file"])
        )
        expected_logs.add(record["log"])

        image_path = image_root / record["image"]
        sidecar_path = image_root / record["sidecar"]
        metadata_path = image_root / record["metadata_file"]
        log_path = log_root / record["log"]
        verify_file(image_path, record["image_size"], record["image_sha256"])
        verify_file(
            sidecar_path, record["sidecar_size"], record["sidecar_sha256"]
        )
        verify_file(
            metadata_path, record["metadata_size"], record["metadata_sha256"]
        )
        verify_file(log_path, record["log_size"], record["log_sha256"])
        verify_sidecar(sidecar_path, record["image"], record["image_sha256"])

        metadata = parse_metadata(metadata_path)
        for field, expected_value in metadata_contract.items():
            if metadata.get(field) != expected_value:
                fail(
                    f"映像說明欄位 {field} 不符：{metadata_path}："
                    f"預期 {expected_value}，實際 {metadata.get(field)}"
                )

        log_text = log_path.read_text(encoding="utf-8", errors="replace")
        for marker in required_log_markers(record):
            if marker not in log_text:
                fail(f"建置日誌缺少完成標記 {marker}：{log_path}")

        listed_size = xz_uncompressed_size(image_path)
        if listed_size != record["uncompressed_size"]:
            fail(
                f"XZ 解壓大小不符：{image_path}：預期 "
                f'{record["uncompressed_size"]}，實際 {listed_size}'
            )
        run_checked(["xz", "-t", "--", str(image_path)])
        print(f'通過：{record["release"]}/{record["profile"]} SHA-256 與 XZ')

    actual_image_files = {
        path.name
        for path in image_root.iterdir()
        if path.is_file()
        and (
            path.name.endswith(".img.xz")
            or path.name.endswith(".img.xz.sha")
            or path.name.endswith(".img.txt")
        )
    }
    if actual_image_files != expected_image_files:
        fail(
            "歷史映像目錄檔案集合不符：缺少 "
            f"{sorted(expected_image_files - actual_image_files)}；多出 "
            f"{sorted(actual_image_files - expected_image_files)}"
        )
    actual_logs = {
        path.name for path in log_root.glob("lamobo-r1-*.log") if path.is_file()
    }
    if actual_logs != expected_logs:
        fail(
            f"R1 歷史日誌集合不符：缺少 {sorted(expected_logs - actual_logs)}；"
            f"多出 {sorted(actual_logs - expected_logs)}"
        )


def text_lines(path: Path) -> set[str]:
    return set(path.read_text(encoding="utf-8", errors="strict").splitlines())


def inspect_representative_content(
    contract: dict[str, Any], image_root: Path
) -> None:
    policy = contract["representative_content_check"]
    matches = [
        record
        for record in contract["archives"]
        if record["release"] == policy["release"]
        and record["profile"] == policy["profile"]
    ]
    if len(matches) != 1:
        fail("找不到唯一的代表映像契約")
    compressed_image = image_root / matches[0]["image"]
    original_stat = compressed_image.stat()
    loop_device: str | None = None
    mounted = False
    primary_error: BaseException | None = None
    cleanup_errors: list[str] = []

    with tempfile.TemporaryDirectory(prefix="bananapi-r1-archive-") as temporary:
        temporary_root = Path(temporary)
        raw_image = temporary_root / "trixie-cli.img"
        mount_root = temporary_root / "root"
        mount_root.mkdir()
        try:
            with raw_image.open("xb") as output:
                run_checked(
                    ["xz", "-dc", "--", str(compressed_image)], stdout=output
                )
            if raw_image.stat().st_size != matches[0]["uncompressed_size"]:
                fail("代表映像解壓大小與契約不符")

            partition_result = run_checked(["sfdisk", "--json", str(raw_image)])
            table = json.loads(partition_result.stdout)["partitiontable"]
            if table.get("label") != policy["partition_table"]:
                fail("代表映像分割表類型不符")
            if table.get("sectorsize") != policy["sector_size"]:
                fail("代表映像 sector 大小不符")
            partitions = table.get("partitions", [])
            if len(partitions) != 1:
                fail("代表映像不是單一分割區")
            partition = partitions[0]
            for field, contract_field in (
                ("start", "partition_start"),
                ("size", "partition_size"),
                ("type", "partition_type"),
            ):
                if partition.get(field) != policy[contract_field]:
                    fail(f"代表映像分割區欄位 {field} 不符")

            run_checked(["sudo", "-n", "true"])
            loop_device = run_checked(
                [
                    "sudo",
                    "losetup",
                    "--find",
                    "--show",
                    "--partscan",
                    "--read-only",
                    str(raw_image),
                ]
            ).stdout.strip()
            run_checked(["sudo", "udevadm", "settle"])
            if run_checked(["lsblk", "-dnro", "RO", loop_device]).stdout.strip() != "1":
                fail("代表映像 loop 裝置不是唯讀")
            partition_lines = run_checked(
                ["lsblk", "-nrpo", "NAME,TYPE", loop_device]
            ).stdout.splitlines()
            partition_paths = [
                line.split()[0]
                for line in partition_lines
                if len(line.split()) == 2 and line.split()[1] == "part"
            ]
            if len(partition_paths) != 1:
                fail("代表映像沒有唯一可檢查分割區")
            partition_path = partition_paths[0]
            filesystem = run_checked(
                ["sudo", "blkid", "-s", "TYPE", "-o", "value", partition_path]
            ).stdout.strip()
            label = run_checked(
                ["sudo", "blkid", "-s", "LABEL", "-o", "value", partition_path]
            ).stdout.strip()
            if filesystem != policy["filesystem"]:
                fail("代表映像檔案系統不符")
            if label != policy["filesystem_label"]:
                fail("代表映像檔案系統標籤不符")

            run_checked(
                ["sudo", "mount", "-o", "ro,noload", partition_path, str(mount_root)]
            )
            mounted = True
            options = run_checked(
                ["findmnt", "-no", "OPTIONS", str(mount_root)]
            ).stdout.strip().split(",")
            if "ro" not in options:
                fail("代表映像未以唯讀方式掛載")

            for relative in policy["required_paths"]:
                path = mount_root / relative
                if not path.exists() or not path.is_file() or path.stat().st_size == 0:
                    fail(f"代表映像缺少必要檔案：{relative}")
            for pattern in policy["required_globs"]:
                paths = list(mount_root.glob(pattern))
                if len(paths) != 1 or not paths[0].is_file() or paths[0].stat().st_size == 0:
                    fail(f"代表映像必要檔案不唯一或為空：{pattern}")
            for relative, required_lines in policy["required_file_lines"].items():
                actual_lines = text_lines(mount_root / relative)
                for required_line in required_lines:
                    if required_line not in actual_lines:
                        fail(f"代表映像 {relative} 缺少：{required_line}")
        except BaseException as error:
            primary_error = error
        finally:
            if mounted:
                result = subprocess.run(
                    ["sudo", "umount", str(mount_root)],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if result.returncode != 0:
                    cleanup_errors.append(f"卸載失敗：{result.stderr.strip()}")
            if loop_device:
                result = subprocess.run(
                    ["sudo", "losetup", "-d", loop_device],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if result.returncode != 0:
                    cleanup_errors.append(f"釋放 loop 失敗：{result.stderr.strip()}")

        if cleanup_errors:
            cleanup_detail = "；".join(cleanup_errors)
            if primary_error:
                raise VerificationError(
                    f"{primary_error}；清理亦失敗：{cleanup_detail}"
                ) from primary_error
            fail(cleanup_detail)
        if primary_error:
            raise primary_error

    final_stat = compressed_image.stat()
    if (
        final_stat.st_size != original_stat.st_size
        or final_stat.st_mtime_ns != original_stat.st_mtime_ns
        or final_stat.st_ino != original_stat.st_ino
    ):
        fail("代表壓縮映像的檔案識別或時間戳在唯讀檢查期間改變")
    print("通過：Trixie CLI 分割區及必要檔案唯讀檢查")


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="核驗 Banana Pi R1 十組歷史映像與代表映像唯讀內容"
    )
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    parser.add_argument("--image-root", type=Path)
    parser.add_argument("--log-root", type=Path)
    return parser.parse_args()


def main() -> int:
    arguments = parse_arguments()
    try:
        contract = load_contract(arguments.contract.resolve())
        image_root = (
            arguments.image_root or Path(contract["archive_roots"]["images"])
        ).resolve()
        log_root = (
            arguments.log_root or Path(contract["archive_roots"]["logs"])
        ).resolve()
        if not image_root.is_dir():
            fail(f"找不到歷史映像目錄：{image_root}")
        if not log_root.is_dir():
            fail(f"找不到歷史建置日誌目錄：{log_root}")
        verify_board_equivalence(contract, arguments.repo_root.resolve())
        print("通過：舊新 R1 板檔關鍵硬體欄位等同性與 EOS 狀態")
        verify_archive_matrix(contract, image_root, log_root)
        inspect_representative_content(contract, image_root)
    except VerificationError as error:
        print(f"驗證失敗：{error}", file=sys.stderr)
        return 1
    print("Banana Pi R1 歷史／封存 L2 證據全部通過；不構成支援或實機聲明。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
