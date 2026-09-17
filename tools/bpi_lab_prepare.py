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
    from . import bpi_lab_disk as disk
else:
    import bpi_lab_image as image
    import bpi_lab_disk as disk

AMLOGIC_BOARDS = {"bpi-cm4io": "bananapicm4io", "bpi-m2pro": "bananapim2pro",
                  "bpi-m2s": "bananapim2s", "bpi-m5": "bananapim5"}
FAMILIES = {name: name for name in ("allwinner", "amlogic", "rockchip", "mediatek", "spacemit")}
FAMILIES.update({name: "special" for name in ("sunplus", "renesas", "realtek", "synaptics")})
BOARD_FAMILIES = {
    "rockchip": ("bpi-forge1", "bpi-aim7", "bpi-cm5pro", "bpi-m1super", "bpi-m5pro",
                 "bpi-m7", "bpi-p2pro", "bpi-r2pro", "bpi-w3"),
    "mediatek": ("bpi-r2", "bpi-r3", "bpi-r3mini", "bpi-r4", "bpi-r4lite", "bpi-r4pro", "bpi-r64"),
    "spacemit": ("bpi-cm6", "bpi-f3", "bpi-sm10"),
    "sunplus": ("bpi-f2p", "bpi-f2s"), "renesas": ("bpi-ai2n",),
    "realtek": ("bpi-m4", "bpi-w2"), "synaptics": ("bpi-m6",),
}


def family_module(family):
    image.require(family in FAMILIES, "家族未接入")
    return importlib.import_module((__package__ + "." if __package__ else "") + "bpi_lab_" + FAMILIES[family])


def prepare_family(family, read_file, *, board, kernel_release, output):
    module = family_module(family)
    return module.prepare(read_file, board=AMLOGIC_BOARDS[board] if family == "amlogic" else board,
                          kernel_release=kernel_release, output=output)


def root_binding(manifest, reader):
    """區分 UUID 與映像內唯一標籤；不宣稱實板其他媒體沒有相同標籤。"""
    expected = manifest.get("root_uuid")
    if expected is not None:
        image.require(expected == reader.filesystem_uuid, "原配組件 root UUID 與超級區塊不符")
        return {"method": "uuid", "uuid": expected}
    label = manifest.get("root_label")
    image.require(isinstance(label, str) and re.fullmatch(r"[A-Za-z0-9_.+-]{1,16}", label)
                  and manifest.get("root_target") == "LABEL=" + label,
                  "原配組件缺少可核對的根 UUID 或標籤")
    image.require(getattr(reader, "filesystem_label", None) == label
                  and reader.report.get("filesystem_label_unique") is True
                  and reader.report.get("filesystem_labels_complete") is True,
                  "根標籤未與超級區塊核對或在映像內不唯一")
    target = manifest.get("root_fstab_target")
    image.require(target in ("UUID=" + reader.filesystem_uuid, "LABEL=" + label),
                  "原配 fstab 與根標籤的超級區塊身分不符")
    return {"method": "label", "label": label, "uuid": reader.filesystem_uuid,
            "unique_in_image": True, "unique_on_hardware": False}


def prepare(source, sha256, *, family, board, kernel_release, output,
            max_raw_bytes=32 * 1024**3, timeout=1800, from_extraction=False, layout="single-ext"):
    image.require(family in FAMILIES, "家族未接入")
    image.require(layout in ("single-ext", "disk"), "映像讀取模式無效")
    image.require(isinstance(board, str) and re.fullmatch(r"bpi-[a-z0-9-]{1,32}", board), "板別代號無效")
    image.require(isinstance(kernel_release, str) and re.fullmatch(r"[A-Za-z0-9_.+~-]{1,128}", kernel_release),
                  "核心版本無效")
    output = image.create_directory(output)
    report = {"schema": "bpi-lab-prepare-v1", "status": "blocked", "family": family,
              "board": board, "kernel_release": kernel_release,
              "hardware_validated": False, "whole_backend_ready": False,
              "media_written": False, "boot_executed": False,
              "source_reread": not from_extraction,
              "layout": layout,
              "limits": "只完成原映像組件處理；仍須核定平台引導、救援、RAM 及媒體配對。"}
    try:
        module = family_module(family)
        candidates = module.POLICIES if family == "allwinner" else AMLOGIC_BOARDS if family == "amlogic" else BOARD_FAMILIES[family]
        image.require(board in candidates,
                      "板別不屬於此家族或尚未接入")
        reader_type = image.SnapshotReader if from_extraction else disk.DiskReader if layout == "disk" else image.ImageReader
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
                report["root_binding"] = root_binding(manifest, reader)
                report["root_identity_verified"] = True
                report["kernel_release"] = manifest.get("kernel_release", kernel_release)
                report["status"] = "prepared"
            report["source"] = reader.report["source_digest"]
            report["raw"] = reader.report["raw"]
            report["partition"] = reader.report["partition"]
        with image.safe.open_root(output / "extraction") as root:
            value, _ = image.safe.fingerprint(root, "extraction.json", limit=4 * 1024**2)
        report["extraction"] = {"path": "extraction/extraction.json", **value}
    except (ValueError, OSError, ImportError, lzma.LZMAError, subprocess.SubprocessError,
            TypeError, KeyError, IndexError, AttributeError) as exc:
        report.update(status="blocked", error=str(exc), error_type=type(exc).__name__)
    finally:
        image.save_json(output, "preparation.json", report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description="從原映像擷取並核對家族原配組件，不操作實板")
    parser.add_argument("image", type=Path)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--family", choices=tuple(FAMILIES), required=True)
    parser.add_argument("--board", required=True)
    parser.add_argument("--kernel-release", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-raw-bytes", type=int, default=32 * 1024**3)
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--from-extraction", action="store_true",
                        help="輸入改為既有 extraction.json，SHA-256 也必須是該證據檔摘要")
    parser.add_argument("--layout", choices=("single-ext", "disk"), default="single-ext",
                        help="單一 MBR ext 或完整 MBR／GPT 多分割核對；不自動降低核對規則")
    args = parser.parse_args(argv)
    try:
        report = prepare(args.image, args.sha256, family=args.family, board=args.board,
                         kernel_release=args.kernel_release, output=args.output,
                         max_raw_bytes=args.max_raw_bytes, timeout=args.timeout,
                         from_extraction=args.from_extraction, layout=args.layout)
    except (ValueError, OSError) as exc:
        report = {"status": "blocked", "error": str(exc), "hardware_validated": False}
    print(json.dumps(report, ensure_ascii=False, allow_nan=False))
    return 0 if report["status"] == "prepared" else 2


if __name__ == "__main__":
    raise SystemExit(main())
