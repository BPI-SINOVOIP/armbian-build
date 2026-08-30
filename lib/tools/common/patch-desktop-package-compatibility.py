#!/usr/bin/env python3
"""修正特定發行版的 armbian-config 桌面套件名稱。"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import tempfile

import yaml


JAMMY_REMOVE = {
    "pipewire-libcamera",
    "gstreamer1.0-libcamera",
    "glmark2-x11",
    "glmark2-es2-x11",
}
JAMMY_ADD = ("glmark2", "glmark2-es2")


def patch_common_yaml(path: Path, release: str) -> bool:
    """套用相容修正；回傳檔案是否有變更。"""

    if release != "jammy":
        return False

    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError(f"無效的 YAML 根節點：{path}")

    tiers = document.get("tiers")
    if not isinstance(tiers, dict):
        raise ValueError(f"找不到 tiers：{path}")

    changed = False
    for tier_name in ("minimal", "mid"):
        tier = tiers.get(tier_name)
        if not isinstance(tier, dict):
            raise ValueError(f"找不到 tiers.{tier_name}：{path}")
        packages = tier.get("packages")
        if not isinstance(packages, list):
            raise ValueError(f"找不到 tiers.{tier_name}.packages：{path}")

        filtered = [package for package in packages if package not in JAMMY_REMOVE]
        if filtered != packages:
            packages[:] = filtered
            changed = True

    mid_packages = tiers["mid"]["packages"]
    for package in JAMMY_ADD:
        if package not in mid_packages:
            mid_packages.append(package)
            changed = True

    if not changed:
        return False

    mode = path.stat().st_mode
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        delete=False,
    ) as temporary:
        yaml.safe_dump(document, temporary, allow_unicode=True, sort_keys=False)
        temporary_path = Path(temporary.name)

    os.chmod(temporary_path, mode)
    os.replace(temporary_path, path)
    return True


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rootfs", required=True, type=Path)
    parser.add_argument("--release", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    common_yaml = (
        args.rootfs
        / "usr"
        / "share"
        / "armbian-config"
        / "desktops"
        / "yaml"
        / "common.yaml"
    )
    if not common_yaml.is_file():
        raise FileNotFoundError(f"找不到 armbian-config 桌面套件定義：{common_yaml}")

    patch_common_yaml(common_yaml, args.release)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
