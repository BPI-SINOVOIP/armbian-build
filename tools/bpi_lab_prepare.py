#!/usr/bin/env python3
"""從已核對完整映像準備家族原配組件；不部署或引導實板。"""

from __future__ import annotations

import argparse
import importlib
import json
import lzma
from pathlib import Path
import re
import subprocess

if __package__:
    from . import bpi_lab_image as image
else:
    import bpi_lab_image as image

AMLOGIC_BOARDS = {"bpi-cm4io": "bananapicm4io", "bpi-m2pro": "bananapim2pro",
                  "bpi-m2s": "bananapim2s", "bpi-m5": "bananapim5"}


def family_module(family):
    image.require(family in ("allwinner", "amlogic"), "此入口只接入 Allwinner ARM32／A64 及 Amlogic")
    return importlib.import_module((__package__ + "." if __package__ else "") + "bpi_lab_" + family)


def prepare_family(family, read_file, *, board, kernel_release, output):
    module = family_module(family)
    return module.prepare(read_file, board=AMLOGIC_BOARDS[board] if family == "amlogic" else board,
                          kernel_release=kernel_release, output=output)


def prepare(source, sha256, *, family, board, kernel_release, output,
            max_raw_bytes=32 * 1024**3, timeout=1800, from_extraction=False):
    image.require(family in ("allwinner", "amlogic"), "家族未接入")
    image.require(isinstance(board, str) and re.fullmatch(r"bpi-[a-z0-9-]{1,32}", board), "板別代號無效")
    image.require(isinstance(kernel_release, str) and re.fullmatch(r"[A-Za-z0-9_.+~-]{1,128}", kernel_release),
                  "核心版本無效")
    output = image.create_directory(output)
    report = {"schema": "bpi-lab-prepare-v1", "status": "blocked", "family": family,
              "board": board, "kernel_release": kernel_release,
              "hardware_validated": False, "whole_backend_ready": False,
              "media_written": False, "boot_executed": False,
              "source_reread": not from_extraction,
              "limits": "只完成原映像組件處理；仍須核定平台引導、救援、RAM 及媒體配對。"}
    try:
        module = family_module(family)
        image.require(board in (module.POLICIES if family == "allwinner" else AMLOGIC_BOARDS),
                      "板別不屬於此家族或尚未接入")
        reader_type = image.SnapshotReader if from_extraction else image.ImageReader
        with reader_type(source, sha256, output / "extraction",
                         max_raw_bytes=max_raw_bytes, timeout=timeout) as reader:
            manifest = prepare_family(family, reader.read_file, board=board,
                                      kernel_release=kernel_release, output=output / "components")
            image.require(type(manifest) is dict and manifest.get("hardware_validated") is False,
                          "家族回報無效或誤宣告硬體驗證")
            status = manifest.get("status")
            image.require(status in ("prepared", "blocked"), "家族回報未明示準備或阻擋狀態")
            blob = (json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode()
            image.save(output, "family-result.json", blob)
            report["components"] = {"path": "family-result.json", **image.digest(blob)}
            report["component_status"] = status
            report["blockers"] = manifest.get("blockers", [])
            report["root_uuid"] = reader.filesystem_uuid
            report["root_uuid_verified"] = manifest.get("root_uuid") == reader.filesystem_uuid
            if status == "prepared":
                image.require(report["root_uuid_verified"], "原配組件 root UUID 與超級區塊不符")
                report["status"] = "prepared"
            report["source"] = reader.report["source_digest"]
            report["raw"] = reader.report["raw"]
            report["partition"] = reader.report["partition"]
        with image.safe.open_root(output / "extraction") as root:
            value, _ = image.safe.fingerprint(root, "extraction.json", limit=4 * 1024**2)
        report["extraction"] = {"path": "extraction/extraction.json", **value}
    except (ValueError, OSError, ImportError, lzma.LZMAError, subprocess.SubprocessError) as exc:
        report.update(status="blocked", error=str(exc))
    finally:
        image.save_json(output, "preparation.json", report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description="從原映像擷取並核對家族原配組件，不操作實板")
    parser.add_argument("image", type=Path)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--family", choices=("allwinner", "amlogic"), required=True)
    parser.add_argument("--board", required=True)
    parser.add_argument("--kernel-release", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-raw-bytes", type=int, default=32 * 1024**3)
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--from-extraction", action="store_true",
                        help="輸入改為既有 extraction.json，SHA-256 也必須是該證據檔摘要")
    args = parser.parse_args(argv)
    try:
        report = prepare(args.image, args.sha256, family=args.family, board=args.board,
                         kernel_release=args.kernel_release, output=args.output,
                         max_raw_bytes=args.max_raw_bytes, timeout=args.timeout,
                         from_extraction=args.from_extraction)
    except (ValueError, OSError) as exc:
        report = {"status": "blocked", "error": str(exc), "hardware_validated": False}
    print(json.dumps(report, ensure_ascii=False, allow_nan=False))
    return 0 if report["status"] == "prepared" else 2


if __name__ == "__main__":
    raise SystemExit(main())
