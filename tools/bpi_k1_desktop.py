#!/usr/bin/env python3
"""從固定 configng 提交準備 K1 桌面，確保主機與 chroot 使用相同內容。"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bpi_k1_board_targets as targets

DEFAULT_PROFILE = Path(__file__).resolve().parents[1] / "config/spacemit-k1-profiles/desktop-noble-gnome.json"
INSTALL_PATH = Path("usr/lib/bpi-k1-configng")
REQUIRED_LIBRARIES = ("initialize", "functions", "docs", "system", "desktops", "network", "software", "runtime")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_profile(path: Path) -> dict:
    profile = json.loads(path.read_text())
    require(profile.get("schema_version") == 1, "桌面設定版本不符")
    require(bool(re.fullmatch(r"[0-9a-f]{40}", profile.get("commit", ""))), "桌面來源必須固定完整 Git 提交")
    require(profile.get("release") == "noble" and profile.get("architecture") == "riscv64", "桌面設定僅接受 Noble／riscv64")
    require(profile.get("desktop") == "gnome" and profile.get("tier") == "minimal", "桌面設定僅接受 GNOME minimal")
    return profile


def fingerprint(profile_path: Path) -> str:
    # 工具與設定一起進入快取身分，避免修補邏輯改變後誤用舊桌面。
    return digest(profile_path.read_bytes() + b"\0" + Path(__file__).read_bytes())


def validate_target(profile: dict, board: str, release: str, arch: str, desktop: str, tier: str) -> None:
    require(targets.resolve_board(board) in profile["boards"], f"桌面設定不接受板型：{board}")
    for key, value in (("release", release), ("architecture", arch), ("desktop", desktop), ("tier", tier)):
        require(profile[key] == value, f"桌面設定不接受 {key}={value}，需要 {profile[key]}")


def inventory(root: Path) -> dict:
    result = {}
    require(root.is_dir() and not root.is_symlink(), "桌面內容目錄不存在或為連結")
    for path in sorted(root.rglob("*")):
        require(not path.is_symlink(), f"桌面內容不接受連結：{path.relative_to(root)}")
        require(path.is_file() or path.is_dir(), f"桌面內容不接受特殊檔案：{path.relative_to(root)}")
        if path.is_file():
            result[path.relative_to(root).as_posix()] = {
                "sha256": digest(path.read_bytes()), "mode": path.stat().st_mode & 0o777,
            }
    return result


def unpack_git_archive(source: Path, commit: str, destination: Path) -> None:
    # 直接讀 Git 物件，工作樹的未提交改動不會混入固定來源。
    archive = subprocess.run(["git", "-C", str(source), "archive", "--format=tar", commit], check=True, capture_output=True).stdout
    with tarfile.open(fileobj=io.BytesIO(archive)) as stream:
        for member in stream:
            relative = PurePosixPath(member.name)
            require(not relative.is_absolute() and ".." not in relative.parts, "來源封存含不安全路徑")
            require(member.isdir() or member.isfile(), f"來源封存不接受連結或特殊檔案：{member.name}")
            target = destination.joinpath(*relative.parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with stream.extractfile(member) as incoming:
                    target.write_bytes(incoming.read())
                target.chmod(member.mode & 0o777)


def patch_gnome(source: Path, profile: dict) -> None:
    path = source / "tools/modules/desktops/yaml/gnome.yaml"
    require(digest(path.read_bytes()) == profile["upstream_gnome_sha256"], "上游 GNOME 定義雜湊不符，拒絕套用修正")
    document = yaml.safe_load(path.read_text())
    noble = document["releases"]["noble"]
    require(noble["architectures"] == ["arm64", "amd64"], "上游 GNOME 架構定義已變更")
    # 只修改隔離副本，保留上游套件、解析器與安裝邏輯。
    noble["architectures"].append("riscv64")
    document["status"] = "community"
    document["description"] = "GNOME－K1 Noble 桌面建置候選"
    for key, additions in (("packages", profile["add_packages"]), ("packages_remove", profile["remove_packages"])):
        values = noble.setdefault(key, [])
        for package in additions:
            if package not in values:
                values.append(package)
    path.write_text(yaml.safe_dump(document, allow_unicode=True, sort_keys=False))


def resolved_desktop(runtime: Path, profile: dict) -> dict:
    directory = runtime / "tools/modules/desktops"
    result = subprocess.run([
        "python3", str(directory / "scripts/parse_desktop_yaml.py"), str(directory / "yaml"),
        profile["desktop"], profile["release"], profile["architecture"], "--tier", profile["tier"],
    ], check=True, capture_output=True, text=True)
    values = {}
    for token in shlex.split(result.stdout):
        key, separator, value = token.partition("=")
        require(bool(separator), "桌面解析器輸出格式不符")
        values[key] = value
    require(values.get("DESKTOP_AVAILABLE") == "yes", "桌面解析器未宣告此架構可用")
    require(values.get("DESKTOP_DM") == "gdm3", "GNOME 顯示管理員必須為 gdm3")
    packages = set(values.get("DESKTOP_PACKAGES", "").split())
    missing = set(profile["required_packages"]) - packages
    require(not missing, "桌面缺少必要套件：" + ", ".join(sorted(missing)))
    require(not (packages & set(profile["remove_packages"])), "桌面仍包含明確排除的套件")
    return values


def verify(prepared: Path, profile_path: Path) -> dict:
    manifest = json.loads((prepared / "manifest.json").read_text())
    require(manifest.get("fingerprint") == fingerprint(profile_path), "桌面快取與目前工具／設定不一致")
    require(manifest.get("files") == inventory(prepared / "runtime"), "桌面內容或權限已偏離來源清單")
    require(manifest.get("resolved") == resolved_desktop(prepared / "runtime", load_profile(profile_path)), "桌面解析結果不一致")
    return manifest


def prepare(source: Path, output: Path, profile_path: Path) -> dict:
    profile = load_profile(profile_path)
    require(not output.is_symlink(), "桌面輸出不得為連結")
    if output.exists():
        return verify(output, profile_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".k1-desktop-", dir=output.parent) as scratch:
        stage = Path(scratch)
        source_copy = stage / "source"
        source_copy.mkdir()
        unpack_git_archive(source, profile["commit"], source_copy)
        patch_gnome(source_copy, profile)
        # 使用該提交自己的組裝程式，避免另寫一套上游模組串接邏輯。
        assembled = subprocess.run(["bash", "tools/config-assemble.sh", "-p"], cwd=source_copy, capture_output=True, text=True)
        require(assembled.returncode == 0, "configng 組裝失敗：" + assembled.stderr[-2000:])
        for name in REQUIRED_LIBRARIES:
            script = source_copy / f"lib/armbian-config/config.{name}.sh"
            require(script.is_file() and script.stat().st_size > 0, f"configng 組裝缺少模組：{name}")
            subprocess.run(["bash", "-n", str(script)], check=True, capture_output=True)
        json.loads((source_copy / "lib/armbian-config/config.jobs.json").read_text())
        product = stage / "product"
        runtime = product / "runtime"
        runtime.mkdir(parents=True)
        for name in ("bin", "lib", "share", "tools/modules/desktops", "tools/modules/system/runner-cleanup"):
            shutil.copytree(source_copy / name, runtime / name)
        shutil.copy2(source_copy / "LICENSE", runtime / "LICENSE")
        manifest = {
            "schema_version": 1, "fingerprint": fingerprint(profile_path),
            "repository": profile["repository"], "commit": profile["commit"],
            "profile_sha256": digest(profile_path.read_bytes()), "tool_sha256": digest(Path(__file__).read_bytes()),
            "upstream_gnome_sha256": profile["upstream_gnome_sha256"],
            "resolved": resolved_desktop(runtime, profile), "files": inventory(runtime),
            "scope": "同一固定來源的桌面解析與安裝內容；官方加速套件及實機通過情形另行記錄。",
        }
        (product / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
        product.rename(output)
    return verify(output, profile_path)


def install(prepared: Path, rootfs: Path, profile_path: Path) -> None:
    verify(prepared, profile_path)
    rootfs = rootfs.resolve()
    require(rootfs != Path("/") and rootfs.is_dir(), "必須指定獨立的目標根檔案系統")
    target = rootfs / INSTALL_PATH
    for parent in (rootfs / "usr", rootfs / "usr/lib", target):
        require(not parent.is_symlink(), "桌面安裝位置不得透過連結跳轉")
    if target.exists():
        require(inventory(target) == inventory(prepared / "runtime"), "目標已有不同桌面內容，拒絕覆寫")
    else:
        shutil.copytree(prepared / "runtime", target)
    require(inventory(target) == inventory(prepared / "runtime"), "安裝後的桌面內容核對失敗")
    evidence = rootfs / "usr/share/doc/bpi-k1-desktop"
    for parent in (rootfs / "usr/share", rootfs / "usr/share/doc", evidence):
        require(not parent.is_symlink(), "桌面證據位置不得透過連結跳轉")
    evidence.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(prepared / "manifest.json", evidence / "manifest.json")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("validate", "fingerprint", "prepare", "install", "verify"):
        sub = commands.add_parser(name)
        sub.add_argument("--profile", type=Path, default=DEFAULT_PROFILE)
        if name == "validate":
            for option in ("board", "release", "arch", "desktop", "tier"):
                sub.add_argument("--" + option, required=True)
        if name == "prepare":
            sub.add_argument("--source", type=Path, required=True)
            sub.add_argument("--output", type=Path, required=True)
        if name in ("install", "verify"):
            sub.add_argument("--prepared", type=Path, required=True)
        if name == "install":
            sub.add_argument("--rootfs", type=Path, required=True)
    args = parser.parse_args()
    try:
        profile = load_profile(args.profile)
        if args.command == "validate":
            validate_target(profile, args.board, args.release, args.arch, args.desktop, args.tier)
        elif args.command == "fingerprint":
            print(fingerprint(args.profile))
        elif args.command == "prepare":
            prepare(args.source, args.output, args.profile)
        elif args.command == "install":
            install(args.prepared, args.rootfs, args.profile)
        else:
            verify(args.prepared, args.profile)
    except (ValueError, OSError, subprocess.CalledProcessError, KeyError) as error:
        parser.exit(1, f"K1 桌面準備失敗：{error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
