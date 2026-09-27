#!/usr/bin/env python3
"""準備可追溯的 K1 加速套件，並唯讀檢查 Noble 根檔案系統。"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bpi_k1_board_targets as targets

DEFAULT_LOCK = Path(__file__).resolve().parents[1] / "config/spacemit-k1-acceleration/noble.lock.json"

# 明列桌面必要元件，避免缺少設定中心、輸入法或 D-Bus 啟動程式。
GNOME_DESKTOP_COMPONENTS = {
    "gnome-session": "/usr/bin/gnome-session",
    "gnome-shell": "/usr/bin/gnome-shell",
    "gjs": "/usr/bin/gjs",
    "gdm3": "/usr/sbin/gdm3",
    "gnome-terminal": "/usr/bin/gnome-terminal",
    "nautilus": "/usr/bin/nautilus",
    "gnome-control-center": "/usr/bin/gnome-control-center",
    "ibus": "/usr/bin/ibus-daemon",
}

# CM6 實機 A/B/A 已確認硬體游標出現方塊，改由 Mutter 合成游標。
GNOME_BOARD_ENVIRONMENT = {
    "bpi-cm6": {"MUTTER_DEBUG_DISABLE_HW_CURSORS": "1"},
}


class AuditError(Exception):
    """來源、套件或根系統不符合已鎖定契約。"""


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AuditError(message)


def paragraphs(text: str) -> list[dict[str, str]]:
    result = []
    for block in re.split(r"\n\s*\n", text.strip()):
        fields: dict[str, str] = {}
        key = ""
        for line in block.splitlines():
            if line.startswith((" ", "\t")) and key:
                fields[key] += "\n" + line
            elif ":" in line:
                key, value = line.split(":", 1)
                fields[key] = value.strip()
        if fields:
            result.append(fields)
    return result


def load_lock(path: Path) -> dict:
    lock = json.loads(path.read_text())
    require(lock.get("schema_version") == 1, "不支援的加速鎖格式")
    require(lock.get("repository") == "https://archive.spacemit.com/bianbu/", "來源必須為已核對的官方 HTTPS 套件庫")
    require(lock.get("target") == {"id": "ubuntu", "codename": "noble", "architecture": "riscv64", "python_abi": "3.12"}, "鎖定目標必須為 Noble／riscv64／Python 3.12")
    for key, package in lock["packages"].items():
        require(key == package["Package"] + "=" + package["Version"], "套件識別與版本不符")
        filename = PurePosixPath(package["Filename"])
        require(not filename.is_absolute() and ".." not in filename.parts and str(filename).startswith("pool/") and filename.suffix == ".deb", "套件路徑不合法")
        require(re.fullmatch(r"[0-9a-f]{64}", package["SHA256"]) is not None, "套件缺少 SHA-256")
        require(package["Architecture"] in ("riscv64", "all"), "套件架構不相容")
        require(package["index"] in lock["sources"], "套件來源未鎖定")
    for source in lock["sources"].values():
        require(source["suite"] in ("noble-porting/snapshots/v2.0", "noble-porting/snapshots/v2.3"), "來源快照不在契約內")
        require(source["component"] in ("main", "universe"), "來源元件不在契約內")
    for profile in lock["profiles"].values():
        require(len(profile["packages"]) == len(set(profile["packages"])), "配套含重複套件")
        require(all(key in lock["packages"] for key in profile["packages"]), "配套引用不存在的套件")
        selected = {lock["packages"][key]["Package"]: lock["packages"][key] for key in profile["packages"]}
        require(len(selected) == len(profile["packages"]), "配套同時引用同名套件的不同版本")
        pairs = {"23.2@6460340": ("23.2-6460340bb2", "22.3.5-bb2"), "24.2@6603887": ("24.2-6603887bb8", "24.01-bb3")}
        require(profile["kernel_pvr"] in pairs, "核心 DDK 不在已核對組合內")
        gpu, mesa = pairs[profile["kernel_pvr"]]
        require(selected["img-gpu-powervr"]["Version"] == gpu, "GPU 使用者態與核心 DDK 不相容")
        for name in ("libegl-mesa0", "libgbm1", "libgl1-mesa-dri", "libglapi-mesa", "libglx-mesa0"):
            require(selected[name]["Version"] == mesa, "Mesa 與 GPU DDK 不在同一配套")
    return lock


def obtain(url: str, path: Path, expected: str, size: int | None = None) -> bytes:
    """先驗證既有快取；下載以暫存檔完成後才原子替換。"""
    if path.exists():
        data = path.read_bytes()
        require(sha256(data) == expected and (size is None or len(data) == size), f"快取雜湊或大小不符：{path.name}")
        return data
    path.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url, timeout=90) as response:
        limit = size if size is not None else 16 * 1024 * 1024
        data = response.read(limit + 1)
    require(sha256(data) == expected and (size is None or len(data) == size), f"下載雜湊或大小不符：{path.name}")
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as temp:
        temp.write(data)
        temp_path = Path(temp.name)
    temp_path.replace(path)
    return data


def verify_sources(lock: dict, lock_path: Path, cache: Path, ids: list[str]) -> dict:
    keyring = lock_path.parent / lock["keyring"]
    require(sha256(keyring.read_bytes()) == lock["keyring_sha256"], "官方索引驗證公鑰雜湊不符")
    verified = {}
    for source_id in sorted(set(lock["packages"][key]["index"] for key in ids)):
        source = lock["sources"][source_id]
        directory = cache / "indices" / source_id
        directory.mkdir(parents=True, exist_ok=True)
        base = lock["repository"] + "dists/" + source["suite"] + "/"
        signed = directory / "InRelease"
        obtain(base + "InRelease", signed, source["inrelease_sha256"])
        result = subprocess.run(["gpgv", "--status-fd", "1", "--keyring", str(keyring.resolve()), str(signed)], capture_output=True, text=True)
        require(result.returncode == 0 and "[GNUPG:] VALIDSIG " + lock["signing_fingerprint"] + " " in result.stdout, f"官方索引簽章不符：{source_id}")
        release = signed.read_text().split("\n\n", 1)[1].split("-----BEGIN PGP SIGNATURE-----", 1)[0]
        release = re.sub(r"(?m)^- ", "", release)
        require(sha256(release.encode()) == source["release_sha256"], f"簽章內的 Release 雜湊不符：{source_id}")
        relative = source["component"] + "/binary-riscv64/Packages.gz"
        hashes = release.split("SHA256:\n", 1)[1].split("\n\n", 1)[0]
        require(any(line.split() == [source["packages_gz_sha256"], line.split()[1], relative] for line in hashes.splitlines() if len(line.split()) == 3), "套件索引未受 Release 的 SHA-256 保護")
        compressed = obtain(base + relative, directory / "Packages.gz", source["packages_gz_sha256"])
        data = gzip.decompress(compressed)
        require(sha256(data) == source["packages_sha256"], "解壓後的套件索引雜湊不符")
        rows = {(r["Package"], r["Version"]): r for r in paragraphs(data.decode())}
        for key in ids:
            item = lock["packages"][key]
            if item["index"] != source_id:
                continue
            row = rows.get((item["Package"], item["Version"]), {})
            for field in ("Architecture", "Filename", "SHA256", "Size", "Depends", "Pre-Depends"):
                require(str(item.get(field, "")) == row.get(field, ""), f"套件鎖與簽章索引不符：{key}／{field}")
        verified[source_id] = {"signature": "已通過", "fingerprint": lock["signing_fingerprint"], "packages_sha256": source["packages_sha256"]}
    return verified


def pin_preferences(packages: list[dict]) -> str:
    text = "# 此配套與核心 DDK 一起更新；通過新配套回歸後再更新鎖定版本。\n"
    for item in packages:
        text += f"\nPackage: {item['Package']}\nPin: version {item['Version']}\nPin-Priority: 1001\n"
        text += f"\nPackage: {item['Package']}\nPin: version *\nPin-Priority: -1\n"
    return text + '\nPackage: *\nPin: origin "archive.spacemit.com"\nPin-Priority: -1\n'


def prepare(lock: dict, lock_path: Path, board: str, cache: Path, output: Path) -> dict:
    ids = lock["profiles"][board]["packages"]
    verified = verify_sources(lock, lock_path, cache, ids)
    artifacts = []
    for key in ids:
        item = lock["packages"][key]
        path = cache / PurePosixPath(item["Filename"]).name
        url = lock["repository"] + urllib.parse.quote(item["Filename"], safe="/~")
        obtain(url, path, item["SHA256"], item["Size"])
        control = subprocess.run(["dpkg-deb", "--field", str(path)], capture_output=True, text=True, check=True)
        row = paragraphs(control.stdout)[0]
        for field in ("Package", "Version", "Architecture", "Depends", "Pre-Depends"):
            require(row.get(field, "") == item.get(field, ""), f"二進位套件控制欄位不符：{key}／{field}")
        artifacts.append({"package": key, "path": str(path.resolve()), "sha256": item["SHA256"], "size": item["Size"]})
    output.mkdir(parents=True, exist_ok=True)
    report = {"board": board, "lock_sha256": sha256(lock_path.read_bytes()), "sources": verified, "artifacts": artifacts, "hardware_verified": False}
    (output / "prepared.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    (output / "packages.txt").write_text("\n".join(ids) + "\n")
    (output / "bpi-k1-acceleration.pref").write_text(pin_preferences([lock["packages"][key] for key in ids]))
    return report


def rooted_path(root: Path, path: str) -> Path:
    """以根系統自己的 / 解讀絕對符號連結，避免誤讀主機檔案。"""
    parts = list(PurePosixPath(path).parts)
    resolved: list[str] = []
    links = 0
    while parts:
        part = parts.pop(0)
        if part in ("/", ".", ""):
            continue
        if part == "..":
            if resolved:
                resolved.pop()
            continue
        candidate = root.joinpath(*resolved, part)
        if candidate.is_symlink():
            links += 1
            require(links <= 40, "根系統內的符號連結形成循環")
            target = os.readlink(candidate)
            if target.startswith("/"):
                resolved = []
            parts = list(PurePosixPath(target).parts) + parts
        else:
            resolved.append(part)
    return root.joinpath(*resolved)


def environment_values(text: str, key: str) -> list[str | None]:
    """唯讀解析指定環境鍵，保留重複或無法解析的項目以便拒絕衝突。"""
    values = []
    pattern = re.compile(r"^\s*(?:export\s+)?" + re.escape(key) + r"\s*=\s*(.*)$")
    for line in text.splitlines():
        match = pattern.match(line)
        if match:
            try:
                tokens = shlex.split(match.group(1), comments=True)
                values.append(tokens[0] if len(tokens) == 1 else None)
            except ValueError:
                values.append(None)
    return values


def version_matches(actual: str, operator: str | None, wanted: str | None) -> bool:
    if not operator:
        return True
    return subprocess.run(["dpkg", "--compare-versions", actual, operator, wanted], capture_output=True).returncode == 0


def package_versions(items: list[dict]) -> dict[str, str]:
    versions = {item["Package"]: item["Version"] for item in items}
    for item in items:
        for provided in item.get("Provides", "").split(","):
            if not provided.strip():
                continue
            match = re.fullmatch(r"\s*([a-z0-9][a-z0-9+.-]*)(?::[a-z0-9-]+)?(?:\s*\(=\s*([^()]+)\))?\s*", provided)
            require(match is not None, "不支援的虛擬套件表示式：" + provided)
            name, version = match.groups()
            versions.setdefault(name, version or "")
    return versions


def dependency_missing(expression: str, available: dict[str, str]) -> list[str]:
    missing = []
    pattern = re.compile(r"^([a-z0-9][a-z0-9+.-]*)(?::[a-z0-9-]+)?(?:\s*\((<<|<=|=|>=|>>)\s*([^()]+)\))?$")
    for group in expression.split(","):
        if not group.strip():
            continue
        satisfied = False
        for alternative in group.split("|"):
            match = pattern.fullmatch(alternative.strip())
            require(match is not None, f"不支援的相依表示式：{alternative}")
            name, operator, wanted = match.groups()
            if name in available and version_matches(available[name], operator, wanted):
                satisfied = True
        if not satisfied:
            missing.append(group.strip())
    return missing


def armbian_board_name(text: str) -> str | None:
    """只讀取單一完整板名設定，拒絕重複、字尾拼接與 shell 運算。"""
    values = re.findall(r"(?m)^[ \t]*(?:export[ \t]+)?BOARD[ \t]*=(.*)$", text)
    if len(values) != 1:
        return None
    match = re.fullmatch(r'''[ \t]*(?:'([A-Za-z0-9_.-]+)'|"([A-Za-z0-9_.-]+)"|([A-Za-z0-9_.-]+))(?:[ \t]+(?:\#.*)?)?''', values[0])
    return next((value for value in match.groups() if value is not None), None) if match else None


def preflight(lock: dict, board: str, root: Path, stage: str, armbian_board: str | None = None) -> dict:
    profile = lock["profiles"][board]
    errors, pending = [], []
    release_path = rooted_path(root, "/etc/os-release")
    release = {}
    for line in release_path.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            key, value = line.split("=", 1)
            values = shlex.split(value)
            release[key] = values[0] if values else ""
    if release.get("ID") != "ubuntu" or release.get("VERSION_CODENAME") != "noble":
        errors.append("根系統必須保留 Ubuntu Noble 身分")
    armbian = rooted_path(root, "/etc/armbian-release")
    if not armbian.is_file():
        errors.append("根系統缺少 Armbian 身分")
    else:
        board_name = armbian_board if armbian_board is not None else ("bananapif3" if board == "bpi-f3" else "bananapicm6")
        if not targets.expected_board(board_name, board) or armbian_board_name(armbian.read_text()) != board_name:
            errors.append("Armbian 板型與加速配套不同")
    installed = {item["Package"]: item for item in paragraphs(rooted_path(root, "/var/lib/dpkg/status").read_text()) if item.get("Status") == "install ok installed"}
    if installed.get("libc6", {}).get("Architecture") != "riscv64":
        errors.append("根系統 libc6 架構必須是 riscv64")
    packages = [lock["packages"][key] for key in profile["packages"]]
    planned = dict(installed)
    if stage == "base":
        planned.update({item["Package"]: item for item in packages})
    available = package_versions(list(planned.values()))
    for item in packages:
        name = item["Package"]
        if stage == "installed" and available.get(name) != item["Version"]:
            errors.append(f"套件版本未符合鎖：{name}={item['Version']}")
        for field in ("Depends", "Pre-Depends"):
            for missing in dependency_missing(item.get(field, ""), available):
                pending.append({"package": name, "dependency": missing})
    selected = {item["Package"] for item in packages}
    for name, item in installed.items():
        if name in selected:
            continue
        for group in item.get("Depends", "").split(","):
            references = {alternative.strip().split(" ", 1)[0].split(":", 1)[0] for alternative in group.split("|")}
            if references & selected:
                for missing in dependency_missing(group, available):
                    pending.append({"package": name, "dependency": missing, "reverse_dependency": True})
    if stage == "installed" and pending:
        errors.append("已安裝配套仍有未滿足的相依條件")
    # 核心本體內的 DDK 字串比檔名或手動宣告更能阻止跨板誤配。
    boot = rooted_path(root, "/boot")
    candidates = [boot / "Image"] + sorted(boot.glob("vmlinuz-*"))
    kernel_versions = set()
    for candidate in candidates:
        image = rooted_path(root, "/boot/" + candidate.name)
        if not image.is_file():
            continue
        data = image.read_bytes()
        if data.startswith(b"\x1f\x8b"):
            data = gzip.decompress(data)
        kernel_versions.update(value.decode() for value in re.findall(rb"(?:23|24)\.2@(?:6460340|6603887)", data))
    if kernel_versions != {profile["kernel_pvr"]}:
        errors.append("核心 DDK 證據缺失或混合版本：" + ",".join(sorted(kernel_versions)))
    configs = sorted(boot.glob("config-*"))
    if not configs:
        errors.append("缺少可核對的核心設定")
    for config in configs:
        text = rooted_path(root, "/boot/" + config.name).read_text()
        for option in lock["required_kernel_options"]:
            if not re.search(r"(?m)^" + re.escape(option) + r"=[ym]$", text):
                errors.append(f"核心設定缺少 {option}：{config.name}")
    if stage == "installed":
        files = ["/usr/lib/libspacemit_ep.so.1.2.2", "/usr/lib/libonnxruntime.so.1.18.1", "/usr/lib/libspacemit_mpp.so.0.0.15", "/usr/lib/libpvr_dri_support.so", "/etc/vulkan/icd.d/powervr_icd.json", "/lib/firmware/linlon-v52_v76-80-2/h264dec.fwb", "/lib/firmware/linlon-v52_v76-80-2/hevcdec.fwb"]
        for file in files:
            if not rooted_path(root, file).is_file():
                errors.append("加速配套檔案缺失：" + file)
        sessions = rooted_path(root, "/usr/share/wayland-sessions")
        if not sessions.is_dir() or not list(sessions.glob("*.desktop")):
            errors.append("缺少 Wayland 桌面工作階段")
        if profile.get("desktop_protocol") == "wayland":
            marker = rooted_path(root, "/etc/bpi-k1-vendor.json")
            desktop = None
            if marker.is_file():
                try:
                    desktop = json.loads(marker.read_text()).get("desktop")
                except (ValueError, AttributeError):
                    errors.append("官方格式根系統標記無法解析：/etc/bpi-k1-vendor.json")
            gnome_session = any(
                rooted_path(root, "/usr/share/wayland-sessions/" + name).is_file()
                for name in ("gnome.desktop", "gnome-wayland.desktop")
            )
            if desktop == "gnome-wayland" or "gnome-shell" in installed or gnome_session:
                if not gnome_session:
                    errors.append("缺少 GNOME Wayland 桌面工作階段")
                for name, executable in GNOME_DESKTOP_COMPONENTS.items():
                    if name not in installed:
                        errors.append("GNOME 桌面必要套件未完成安裝：" + name)
                    path = rooted_path(root, executable)
                    if not path.is_file() or not path.stat().st_mode & 0o111:
                        errors.append("GNOME 桌面必要程式缺失或不可執行：" + executable)
                required_environment = GNOME_BOARD_ENVIRONMENT.get(board, {})
                if required_environment:
                    environment = rooted_path(root, "/etc/environment")
                    text = environment.read_text() if environment.is_file() else ""
                    for key, value in required_environment.items():
                        if environment_values(text, key) != [value]:
                            errors.append(f"GNOME 板級相容設定缺失或衝突：/etc/environment 須唯一設定 {key}={value}")
    result = {"board": board, "stage": stage, "passed": not errors, "errors": errors, "pending_dependencies": pending, "kernel_pvr_found": sorted(kernel_versions), "hardware_verified": False, "runtime_status": lock["runtime_status"]}
    if armbian_board is not None and targets.target_for(armbian_board):
        result["armbian_board"] = armbian_board
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lock", type=Path, default=DEFAULT_LOCK, help="加速配套鎖")
    sub = parser.add_subparsers(dest="command", required=True)
    prep = sub.add_parser("prepare", help="驗證官方簽章並準備限定套件")
    prep.add_argument("--board", choices=("bpi-f3", "bpi-cm6"), required=True)
    prep.add_argument("--cache", type=Path, required=True, help="套件與索引快取")
    prep.add_argument("--output", type=Path, required=True, help="準備報告與版本優先序")
    audit = sub.add_parser("preflight", help="唯讀檢查候選根系統")
    audit.add_argument("--board", choices=("bpi-f3", "bpi-cm6"), required=True)
    audit.add_argument("--armbian-board", help="核對完整官方格式板名；省略時僅接受原始板名")
    audit.add_argument("--rootfs", type=Path, required=True, help="已掛載的候選根系統")
    audit.add_argument("--stage", choices=("base", "installed"), default="installed", help="安裝前或安裝後的檢查階段")
    args = parser.parse_args()
    try:
        lock = load_lock(args.lock)
        if args.command == "prepare":
            report = prepare(lock, args.lock, args.board, args.cache, args.output)
        else:
            report = preflight(lock, args.board, args.rootfs.resolve(), args.stage, args.armbian_board)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report.get("passed", True) else 2
    except (AuditError, OSError, ValueError, subprocess.SubprocessError) as exc:
        print(json.dumps({"passed": False, "error": "配套檢查失敗：" + str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
