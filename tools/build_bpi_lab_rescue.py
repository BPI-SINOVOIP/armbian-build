#!/usr/bin/env python3
"""依明確板級配置在同架構原 Linux 建置救援；不安裝、不部署。"""

import json
from pathlib import Path
import subprocess
import sys

if __package__:
    from . import build_bpi_h618_rescue as builder
    from . import bpi_h618_artifacts as safe
else:
    import build_bpi_h618_rescue as builder
    import bpi_h618_artifacts as safe


def load_profile(path, expected):
    path = Path(path).absolute()
    with safe.open_root(path.parent) as root:
        metadata, blob = safe.fingerprint(root, path.name, limit=65536, keep=True)
    builder.require(metadata["sha256"] == expected, "板級救援配置摘要不符")
    profile = safe.parse_manifest(blob)
    return builder.validate_profile(profile)


def main(argv=None):
    parser = builder.parser()
    parser.description = __doc__
    parser.add_argument("--profile", required=True, help="同架構、同板 DT、模組及韌體的固定配置")
    parser.add_argument("--profile-sha256", required=True, help="板級配置 SHA-256")
    parser.add_argument("--check-only", action="store_true", help="只檢查原生建置前提，不新增產物")
    args = parser.parse_args(argv)
    try:
        profile = load_profile(args.profile, args.profile_sha256)
        if args.check_only:
            builder.preflight(args, profile=profile)
            result = {"status": "建置前提通過", "hardware_validated": False, "media_written": False}
        else:
            result = builder.build(args, profile=profile)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        print(json.dumps({"status": "blocked", "error": str(exc), "hardware_validated": False}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
