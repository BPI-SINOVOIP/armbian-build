#!/usr/bin/env python3
"""以兩階段整合 Armbian 管理中的根系統；不掛載、不執行 APT、不設定媒體政策。"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))
import bpi_k1_acceleration as acceleration
import prepare_bpi_k1_vendor_rootfs as common
import bpi_k1_board_targets as targets
import bpi_cm6_gpio as gpio_packages

DEFAULT_LOCK = REPO / "config/spacemit-k1-acceleration/noble.lock.json"
CONNECTIVITY_LOCK = REPO / "config/spacemit-k1-connectivity/source-lock.json"
CM6_HCI_STATE = "var/lib/systemd/rfkill/platform-d4017100.uart:bluetooth"
DESKTOP_PACKAGES = [*acceleration.GNOME_DESKTOP_COMPONENTS,
                    "mesa-utils", "vulkan-tools", "python3-numpy", "python3-pil", "python3-opencv",
                    "cloud-guest-utils", "gdisk", "e2fsprogs"]

# 只接受本次 Noble 的服務布局；忽略說明文字，所有執行與順序指令均須相符。
SSH_UNIT_CONTRACTS = {
    "ssh.service": """[Unit]
After=network.target auditd.service
ConditionPathExists=!/etc/ssh/sshd_not_to_be_run
[Service]
EnvironmentFile=-/etc/default/ssh
ExecStartPre=/usr/sbin/sshd -t
ExecStart=/usr/sbin/sshd -D $SSHD_OPTS
ExecReload=/usr/sbin/sshd -t
ExecReload=/bin/kill -HUP $MAINPID
KillMode=process
Restart=on-failure
RestartPreventExitStatus=255
Type=notify
RuntimeDirectory=sshd
RuntimeDirectoryMode=0755
[Install]
WantedBy=multi-user.target
Alias=sshd.service
""",
    "ssh.socket": """[Unit]
Before=sockets.target ssh.service
ConditionPathExists=!/etc/ssh/sshd_not_to_be_run
[Socket]
ListenStream=0.0.0.0:22
ListenStream=[::]:22
BindIPv6Only=ipv6-only
Accept=no
FreeBind=yes
[Install]
WantedBy=sockets.target
RequiredBy=ssh.service
""",
}
SSH_HOSTKEY_HELPER = r'''#!/usr/bin/python3
"""在 SSH 啟動前生成缺少的主機金鑰；既有有效金鑰不得更換。"""
import os
from pathlib import Path
import stat
import subprocess
import sys

KINDS = ("rsa", "ecdsa", "ed25519")

def require(value, message):
    if not value:
        raise ValueError(message)

def checked_files(root):
    directory = root / "etc/ssh"
    for path in (root, root / "etc", directory):
        require(not path.is_symlink() and path.is_dir(), "SSH 金鑰目錄不可為連結或未知布局")
    allowed = {"ssh_host_" + kind + "_key" + suffix for kind in KINDS for suffix in ("", ".pub")}
    for path in directory.glob("ssh_host_*"):
        info = path.lstat()
        require(path.name in allowed and stat.S_ISREG(info.st_mode) and info.st_nlink == 1,
                "SSH 主機金鑰包含連結或未知檔案")
        require(info.st_uid == os.geteuid() and info.st_size > 0, "SSH 主機金鑰擁有者或長度不符")
        require(info.st_mode & (0o022 if path.suffix == ".pub" else 0o077) == 0,
                "SSH 主機金鑰權限不符")
    return directory

def validate_pairs(directory, complete=False):
    for kind in KINDS:
        private = directory / ("ssh_host_" + kind + "_key")
        public = Path(str(private) + ".pub")
        require(private.exists() == public.exists(), "SSH 主機金鑰配對不完整")
        if not private.exists():
            require(not complete, "SSH 主機金鑰尚未建立")
            continue
        result = subprocess.run(["/usr/bin/ssh-keygen", "-y", "-P", "", "-f", str(private)],
                                capture_output=True, text=True)
        require(result.returncode == 0, "既有 SSH 主機私鑰無法驗證；拒絕覆寫")
        require(result.stdout.split()[:2] == public.read_text().split()[:2],
                "既有 SSH 主機公私鑰不相符；拒絕覆寫")

def setup(root=Path("/")):
    directory = checked_files(root)
    validate_pairs(directory)
    command = ["/usr/bin/ssh-keygen", "-A"]
    if root != Path("/"):
        command += ["-f", str(root)]
    result = subprocess.run(command, capture_output=True)
    require(result.returncode == 0, "SSH 主機金鑰產生失敗，禁止啟動 SSH")
    validate_pairs(checked_files(root), complete=True)

if __name__ == "__main__":
    try:
        require(len(sys.argv) == 1, "此首次啟動工具不接受參數")
        setup()
    except (OSError, ValueError, UnicodeError):
        print("SSH 主機金鑰守門失敗；未啟動 SSH，亦未覆寫既有金鑰。", file=sys.stderr)
        raise SystemExit(1)
'''
SSH_HOSTKEY_UNIT = """[Unit]
Description=K1 首次啟動 SSH 主機金鑰守門
DefaultDependencies=no
After=local-fs.target systemd-random-seed.service
Before=ssh.service ssh.socket shutdown.target
Conflicts=shutdown.target
RequiresMountsFor=/etc/ssh

[Service]
Type=oneshot
ExecStart=/usr/lib/bpi-k1-native/ssh-hostkeys
RemainAfterExit=yes
TimeoutStartSec=2min
UMask=0077
"""
SSH_HOSTKEY_DEPENDENCY = """[Unit]
Requires=bpi-k1-ssh-hostkeys.service
After=bpi-k1-ssh-hostkeys.service
"""


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(path):
    return common.sha256(common.regular(path))


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


def write_json(path, value):
    require(not path.is_symlink(), "紀錄目的地不能是符號連結")
    path.write_bytes(json_bytes(value))


def build_id(value):
    require(re.fullmatch(r"[A-Za-z0-9_.-]{1,96}", value) is not None and value not in (".", ".."),
            "建置識別只能含安全字元，長度須為 1 至 96")
    return value


def root_directory(value):
    path = Path(value)
    require(path.is_dir() and not path.is_symlink(), "根系統必須是既有的一般目錄")
    path = path.resolve()
    require(path != Path("/"), "拒絕操作建置主機的根目錄")
    return path


def binding(root):
    info = root.stat()
    return {"device": info.st_dev, "inode": info.st_ino}


def writable_path(root, relative, *, leaf_link=False):
    """寫入範圍拒絕目錄連結；只允許明確處理的服務檔本身是連結。"""
    parts = Path(relative).parts
    require(parts and not Path(relative).is_absolute() and ".." not in parts,
            "根系統寫入路徑不合法")
    path = root
    for index, part in enumerate(parts):
        path /= part
        if index == len(parts) - 1 and leaf_link:
            continue
        require(not path.is_symlink(), "根系統寫入路徑包含符號連結：" + relative)
        if os.path.lexists(path):
            require(path.is_dir() if index < len(parts) - 1 else path.is_file() or path.is_dir(),
                    "根系統寫入路徑不是一般檔案或目錄：" + relative)
    return path


def root_file(root, relative):
    return common.regular(acceleration.rooted_path(root, "/" + relative.lstrip("/")))


def installed_packages(root):
    rows = acceleration.paragraphs(root_file(root, "var/lib/dpkg/status").read_text())
    incomplete = [row.get("Package", "?") for row in rows
                  if row.get("Status", "").startswith("install ") and row.get("Status") != "install ok installed"]
    require(not incomplete, "根系統有未完成安裝的套件：" + ",".join(incomplete))
    return sorted(({"package": row["Package"], "version": row["Version"],
                    "architecture": row.get("Architecture", ""), "source": row.get("Source", row["Package"])}
                   for row in rows if row.get("Status") == "install ok installed"),
                  key=lambda row: (row["package"], row["architecture"]))


def validate_root(root, board, lock, armbian_board=None):
    require(board in common.BOARDS, "不支援的板型")
    armbian_board = common.BOARDS[board][0] if armbian_board is None else armbian_board
    require(targets.expected_board(armbian_board, board), "Armbian 板名與實體板配套不符")
    values = {}
    for line in root_file(root, "etc/os-release").read_text().splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            parsed = shlex.split(value)
            values[key] = parsed[0] if parsed else ""
    require(values.get("ID") == "ubuntu" and values.get("VERSION_CODENAME") == "noble",
            "根系統不是 Ubuntu Noble")
    armbian = root_file(root, "etc/armbian-release").read_text()
    require(acceleration.armbian_board_name(armbian) == armbian_board, "Armbian 板型與指定配套不符")
    packages = installed_packages(root)
    require(any(row["package"] == "libc6" and row["architecture"] == "riscv64" for row in packages),
            "根系統 libc6 架構不是 riscv64")
    release = lock["profiles"][board]["kernel_release"]
    require(release == common.BOARDS[board][1], "核心版本不在板型契約內")
    for name in ("vmlinuz-" + release, "config-" + release):
        root_file(root, "boot/" + name)
    return packages


def kernel_evidence(root, board, lock, packages):
    release = lock["profiles"][board]["kernel_release"]
    files = {}
    for name in ("vmlinuz-" + release, "config-" + release):
        path = root_file(root, "boot/" + name)
        files["/boot/" + name] = {"bytes": path.stat().st_size, "sha256": digest(path)}
    return {"release": release, "files": files,
            "packages": [row for row in packages if row["package"].startswith(("linux-image-", "linux-dtb-", "linux-headers-"))],
            "scope": "記錄框架根系統中的核心檔案及套件；來源編譯證據須由框架另外保存。"}


def deb_record(path, expected, kind, origin, expected_size, expected_sha256):
    control = subprocess.check_output(["dpkg-deb", "--field", str(path)], text=True)
    rows = acceleration.paragraphs(control)
    require(len(rows) == 1, "DEB 控制資料不完整")
    fields = rows[0]
    require(all(fields.get(key) == value for key, value in expected.items()),
            "DEB 控制欄位與來源鎖不符：" + path.name)
    size, sha = path.stat().st_size, digest(path)
    require(size == int(expected_size) and sha == expected_sha256,
            "DEB 在來源核對期間發生變更：" + path.name)
    return {"filename": path.name, "package": fields["Package"], "version": fields["Version"],
            "architecture": fields["Architecture"], "bytes": size,
            "sha256": sha, "kind": kind, "origin": origin}


def stage(board, rootfs, work_dir, run_id, deb_cache, lock_path=DEFAULT_LOCK,
          bluetooth_path=None, camera_cache=None, armbian_board=None, gpio_cache=None):
    root = root_directory(rootfs)
    run_id = build_id(run_id)
    work = Path(work_dir).absolute()
    require(not os.path.lexists(work), "兩階段工作目錄已存在，拒絕覆寫")
    require(not work.resolve().is_relative_to(root), "工作紀錄不可放入根系統內")
    lock_path = common.regular(lock_path)
    lock_data = lock_path.read_bytes()
    lock = acceleration.load_lock(lock_path)
    initial_packages = validate_root(root, board, lock, armbian_board)
    base_preflight = acceleration.preflight(lock, board, root, "base", armbian_board)
    require(base_preflight["passed"], "整合前根系統預檢失敗：" + "; ".join(base_preflight["errors"]))
    selected = common.package_selection(lock, board, Path(deb_cache))
    bt = common.bluetooth_package(board, bluetooth_path)
    camera = common.camera_packages(board, Path(camera_cache) if camera_cache is not None else None)
    camera_records = common.camera_package_records(camera_cache) if camera else {}
    require((board == "bpi-cm6") == (gpio_cache is not None),
            "CM6 必須提供共同 GPIO 套件；其他板型不得套用")
    gpio_records = gpio_packages.package_records(gpio_cache) if gpio_cache is not None else {}
    verified = acceleration.verify_sources(lock, lock_path, Path(deb_cache), lock["profiles"][board]["packages"])
    require(lock_path.read_bytes() == lock_data, "來源鎖在核對期間變更")
    records = []
    for key, path in zip(lock["profiles"][board]["packages"], selected):
        item = lock["packages"][key]
        records.append(deb_record(path, {name: item[name] for name in ("Package", "Version", "Architecture")},
                                  "acceleration", {"url": lock["repository"] + item["Filename"],
                                                   "signature_verified": True, "index": item["index"]},
                                  item["Size"], item["SHA256"]))
    identity = {"source_kind": "armbian-native-rootfs", "board": board, "build_id": run_id,
                "acceleration_lock_sha256": hashlib.sha256(lock_data).hexdigest()}
    target_profile = targets.target_for(armbian_board) if armbian_board is not None else None
    if target_profile:
        require(bool(camera) == (target_profile["camera_profile"] == "dual-imx415"),
                "暫存套件與官方格式別名的相機配套不符")
        require(board != "bpi-cm6" or bt is not None, "CM6 官方格式別名缺少固定藍牙套件")
        identity["armbian_board"] = armbian_board
    connectivity_data = None
    if bt:
        selected.append(bt[0])
        records.append(deb_record(bt[0], {"Package": "bpi-cm6-bluetooth", "Version": bt[1]["version"],
                                         "Architecture": "riscv64"}, "bluetooth", {"source": bt[1]["source"],
                                         "firmware_source": bt[1]["firmware_source"]}, bt[1]["bytes"], bt[1]["sha256"]))
        identity["cm6_bluetooth_package_sha256"] = bt[1]["sha256"]
        connectivity_data = CONNECTIVITY_LOCK.read_bytes()
        require(hashlib.sha256(connectivity_data).hexdigest() == bt[1]["source_lock_sha256"],
                "藍牙套件與目前來源鎖不符")
        identity["connectivity_lock_sha256"] = hashlib.sha256(connectivity_data).hexdigest()
    for path in camera:
        name = path.name.split("_", 1)[0]
        item = camera_records[name]
        selected.append(path)
        records.append(deb_record(path, {"Package": name, "Version": item["version"], "Architecture": "riscv64"},
                                  "camera", item["provenance"],
                                  item["size"], item["sha256"]))
    if camera:
        identity["cm6_camera_packages"] = camera_records
    if gpio_records:
        for path, item in zip(gpio_packages.paths(gpio_cache), gpio_records.values()):
            selected.append(path)
            records.append(deb_record(path, {"Package": item["package"], "Version": item["version"],
                                             "Architecture": "riscv64"}, "gpio", item["source"],
                                      item["bytes"], item["sha256"]))
        identity["cm6_gpio_packages"] = gpio_records
    require(len({record["filename"] for record in records}) == len(records), "待安裝 DEB 檔名重複")
    relative = "var/tmp/bpi-k1-native-" + run_id
    target = writable_path(root, relative)
    require(not os.path.lexists(target), "本次根系統暫存路徑已存在")
    created_work = created_stage = False
    try:
        work.mkdir(parents=True)
        created_work = True
        target.mkdir(parents=True)
        created_stage = True
        for path, record in zip(selected, records):
            destination = target / record["filename"]
            shutil.copyfile(path, destination)
            require(destination.stat().st_size == record["bytes"] and digest(destination) == record["sha256"],
                    "複製期間 DEB 內容變更")
        (work / "acceleration.lock.json").write_bytes(lock_data)
        if connectivity_data is not None:
            (work / "connectivity.lock.json").write_bytes(connectivity_data)
            write_json(work / "bluetooth-package.json", bt[1])
            identity["cm6_bluetooth_manifest_sha256"] = digest(work / "bluetooth-package.json")
        state = {"schema_version": 1, "source_kind": "armbian-native-rootfs", "status": "staged",
                 "identity": identity, "stage_path": "/" + relative,
                 "install_args": [*DESKTOP_PACKAGES, *("/" + relative + "/" + item["filename"] for item in records)],
                 "packages": records, "verified_sources": verified, "base_preflight": base_preflight,
                 "kernel": kernel_evidence(root, board, lock, initial_packages),
                 "bindings": {"rootfs": binding(root), "lock_path": str(lock_path),
                              "producer_sha256": digest(Path(__file__)),
                              "acceleration_tool_sha256": digest(Path(acceleration.__file__)),
                              "common_tool_sha256": digest(Path(common.__file__)),
                              "gpio_tool_sha256": digest(Path(gpio_packages.__file__)),
                              "runtime_tool_sha256": digest(REPO / "tools/collect_bpi_k1_runtime.py")},
                 "hardware_validation": "pending"}
        if target_profile:
            state["bindings"]["board_targets_tool_sha256"] = digest(Path(targets.__file__))
            state["bindings"]["board_targets_lock_sha256"] = digest(REPO / targets.REGISTRY)
        write_json(work / "stage.json", state)
        write_json(target / "transaction.json", {"stage_sha256": digest(work / "stage.json"), "build_id": run_id})
        return state
    except BaseException:
        if created_stage:
            shutil.rmtree(target)
        if created_work:
            shutil.rmtree(work)
        raise


def verify_bluetooth_files(root, connectivity):
    for name in ("firmware", "firmware_config"):
        item = connectivity["hardware_contract"][name]
        path = root_file(root, item["path"])
        require(path.stat().st_size == item["bytes"] and digest(path) == item["sha256"],
                "安裝後藍牙韌體內容與來源鎖不符")


def seed_bluetooth_initial_policy(root, board, identity):
    """只在新 CM6 映像預置 HCI 偏好；開機及套件升級不執行此操作。"""
    result = {"status": "not-applicable", "hardware_validation": "pending",
              "scope": "僅為新映像預置首次偏好候選；未證明首次開機缺陷已修復。"}
    if board != "bpi-cm6" or "cm6_bluetooth_package_sha256" not in identity:
        return result
    require(identity.get("board") == board and
            re.fullmatch(r"[0-9a-f]{64}", identity["cm6_bluetooth_package_sha256"]) is not None and
            identity.get("connectivity_lock_sha256") == digest(CONNECTIVITY_LOCK),
            "藍牙首次偏好只接受已核對的 CM6 固定配套")
    contract = json.loads(CONNECTIVITY_LOCK.read_text())["hardware_contract"]
    require(contract["compatible"] == "bananapi,bpi-cm6" and
            contract["uart_of_path"] == "/soc/uart@d4017100", "首次偏好的固定 HCI 路徑已不適用")
    target = writable_path(root, CM6_HCI_STATE)
    directory = target.parent
    result.update(path="/" + CM6_HCI_STATE, policy="seed-only-when-no-bluetooth-preference")
    previous = {}
    if directory.exists():
        require(directory.is_dir(), "rfkill 偏好位置不是一般目錄")
        types = {"wlan", "bluetooth", "uwb", "wimax", "wwan", "gps", "fm", "nfc"}
        for entry in sorted(directory.iterdir()):
            require(not entry.is_symlink() and entry.is_file() and entry.stat().st_nlink == 1,
                    "rfkill 偏好目錄含連結或未知布局，拒絕預置")
            require(entry.name.rsplit(":", 1)[-1] in types, "rfkill 偏好檔名無法識別，拒絕預置")
            require(entry.stat().st_size in (1, 2), "rfkill 偏好內容長度異常，拒絕預置")
            data = entry.read_bytes()
            require(data in (b"0", b"1", b"0\n", b"1\n"), "rfkill 偏好內容不是合法 0 或 1，拒絕預置")
            if entry.name == "bluetooth" or entry.name.endswith(":bluetooth"):
                previous[entry.name] = {"soft_blocked": int(data.strip()), "sha256": digest(entry)}
    if previous:
        result.update(status="preserved", existing_bluetooth_preferences=previous)
        return result
    directory.mkdir(parents=True, exist_ok=True)
    # 排他建立及不跟隨連結；後續使用者改為 1 後，本工具不會再覆寫。
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            os.fchown(stream.fileno(), 0, 0)
            os.fchmod(stream.fileno(), 0o644)
            stream.write(b"0\n")
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        target.unlink()
        raise
    result.update(status="seeded", contents="0\n", uid=0, gid=0, mode="0644", sha256=digest(target))
    return result


def configure(root, board, state, work, packages):
    """只配置共用軟體；不呼叫媒體保護、不更改 fstab 或擴容服務。"""
    regular_paths = ("etc/environment", "etc/X11/default-display-manager", "etc/gdm3/custom.conf",
                     "etc/apt/preferences.d/bpi-k1-native", "etc/bpi-k1-native.json",
                     "usr/local/sbin/collect_bpi_k1_runtime.py", "usr/share/bpi-k1-acceleration.lock.json",
                     "usr/share/bpi-cm6-bluetooth/package-manifest.json",
                     "etc/udev/rules.d/99-video-permissions.rules", "etc/udev/rules.d/99-bpi-cm6-camera.rules")
    for relative in regular_paths:
        writable_path(root, relative)
    display = writable_path(root, "etc/systemd/system/display-manager.service", leaf_link=True)
    if "cm6_camera_packages" in state["identity"]:
        writable_path(root, "etc/systemd/system/camera.service", leaf_link=True)
        for wanted in (root / "etc/systemd/system").glob("*.wants/camera.service"):
            writable_path(root, wanted.relative_to(root).as_posix(), leaf_link=True)
        common.configure_camera_permissions(root)
    if "cm6_bluetooth_package_sha256" in state["identity"]:
        common.write(root / "usr/share/bpi-cm6-bluetooth/package-manifest.json",
                     (work / "bluetooth-package.json").read_text())
    common.configure_gnome_environment(root, board, "gnome-wayland")
    common.write(root / "etc/X11/default-display-manager", "/usr/sbin/gdm3\n")
    common.write(root / "etc/gdm3/custom.conf", "[daemon]\nWaylandEnable=true\n[security]\n[xdmcp]\n[chooser]\n[debug]\n")
    display.parent.mkdir(parents=True, exist_ok=True)
    display.unlink(missing_ok=True)
    display.symlink_to("/lib/systemd/system/gdm3.service")
    pinned = {item["package"]: item["version"] for item in state["packages"]}
    for item in packages:
        if item["package"].startswith(("linux-image-", "linux-dtb-", "linux-u-boot-", "armbian-bsp-", "armbian-config")):
            pinned[item["package"]] = item["version"]
    preferences = "# 原生 K1 配套須經整組驗證後更新；此檔不變更媒體安裝政策。\n"
    for name, version in sorted(pinned.items()):
        preferences += f"Package: {name}\nPin: version {version}\nPin-Priority: 1001\n\n"
    common.write(root / "etc/apt/preferences.d/bpi-k1-native", preferences)
    runtime = root / "usr/local/sbin/collect_bpi_k1_runtime.py"
    runtime.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(REPO / "tools/collect_bpi_k1_runtime.py", runtime)
    runtime.chmod(0o755)
    destination = root / "usr/share/bpi-k1-acceleration.lock.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(work / "acceleration.lock.json", destination)
    write_json(root / "etc/bpi-k1-native.json", {**state["identity"], "desktop": "gnome-wayland",
                                               "hardware_validation": "pending"})
    return {"pinned_packages": pinned,
            "bluetooth_initial_policy": seed_bluetooth_initial_policy(root, board, state["identity"])}


def cleanup_desktop_runtime(root):
    """核對建置用桌面副本後移除，僅保留獨立的來源追溯紀錄。"""
    target = writable_path(root, "usr/lib/bpi-k1-configng")
    if not target.exists():
        return {"status": "absent"}
    require(target.is_dir(), "建置用桌面副本不是一般目錄")
    evidence = writable_path(root, "usr/share/doc/bpi-k1-desktop/manifest.json")
    require(evidence.is_file(), "缺少建置用桌面來源紀錄，拒絕清除")
    import bpi_k1_desktop as desktop
    manifest = json.loads(evidence.read_text())
    profile = desktop.load_profile(desktop.DEFAULT_PROFILE)
    require(manifest.get("schema_version") == 1 and
            manifest.get("fingerprint") == desktop.fingerprint(desktop.DEFAULT_PROFILE) and
            manifest.get("profile_sha256") == digest(desktop.DEFAULT_PROFILE) and
            manifest.get("tool_sha256") == digest(Path(desktop.__file__)) and
            manifest.get("repository") == profile["repository"] and manifest.get("commit") == profile["commit"],
            "建置用桌面來源紀錄與固定配套不符")
    require(manifest.get("files") == desktop.inventory(target),
            "建置用桌面副本內容或權限已變更，拒絕清除")
    proof = {"status": "removed", "path": "/usr/lib/bpi-k1-configng",
             "source_manifest": "/usr/share/doc/bpi-k1-desktop/manifest.json",
             "source_manifest_sha256": digest(evidence), "files": len(manifest["files"])}
    shutil.rmtree(target)
    return proof


def unit_directives(text):
    """保留重複指令的先後次序；不接受續行或無法辨識的服務語法。"""
    result, section = {}, None
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if re.fullmatch(r"\[[A-Za-z]+\]", line):
            section = line[1:-1]
            continue
        require(section is not None and "=" in line and not line.endswith("\\"), "SSH 服務包含未知語法")
        name, value = line.split("=", 1)
        if section == "Unit" and name in ("Description", "Documentation"):
            continue
        result.setdefault((section, name), []).append(value)
    return result


def prepare_ssh_host_keys(root):
    """移除建置時金鑰，讓每台裝置在 sshd／socket 啟動前建立專屬金鑰。"""
    root = root_directory(root)
    enabled = {"sockets.target.wants/ssh.socket": "/usr/lib/systemd/system/ssh.socket",
               "ssh.service.requires/ssh.socket": "/usr/lib/systemd/system/ssh.socket",
               "multi-user.target.wants/armbian-firstrun.service": "/usr/lib/systemd/system/armbian-firstrun.service"}
    for relative, expected in enabled.items():
        path = writable_path(root, "etc/systemd/system/" + relative, leaf_link=True)
        require(path.is_symlink() and str(path.readlink()) == expected,
                "SSH socket 或 Armbian 首次啟動的啟用布局不符")
    for base in ("etc/systemd/system", "run/systemd/system", "usr/lib/systemd/system"):
        for name in ("service.d", "socket.d", "sshd.service.d"):
            path = writable_path(root, base + "/" + name)
            require(not path.exists() or (path.is_dir() and not any(path.iterdir())),
                    "SSH 套用範圍包含未驗證的共用 drop-in")
    units = {**SSH_UNIT_CONTRACTS,
             "armbian-firstrun.service": (REPO / "packages/bsp/common/lib/systemd/system/armbian-firstrun.service").read_text()}
    unit_sources = {}
    for name, expected in units.items():
        path = writable_path(root, "usr/lib/systemd/system/" + name)
        require(path.is_file() and unit_directives(path.read_text()) == unit_directives(expected),
                "SSH 或 Armbian 首次啟動服務布局不在已驗證契約內：" + name)
        unit_sources[name] = digest(path)
        for base in ("etc/systemd/system", "run/systemd/system", "usr/lib/systemd/system"):
            override = writable_path(root, base + "/" + name)
            require(base == "usr/lib/systemd/system" or not override.exists(), "SSH 服務存在未驗證的覆寫")
            dropins = writable_path(root, base + "/" + name + ".d")
            require(not dropins.exists() or (dropins.is_dir() and not any(dropins.iterdir())),
                    "SSH 服務存在未驗證的 drop-in")
    firstrun = writable_path(root, "usr/lib/armbian/armbian-firstrun")
    require(firstrun.read_bytes() == (REPO / "packages/bsp/common/usr/lib/armbian/armbian-firstrun").read_bytes(),
            "Armbian 首次啟動腳本與框架來源不符")
    defaults = writable_path(root, "etc/default/armbian-firstrun")
    original_defaults = (REPO / "packages/bsp/common/etc/default/armbian-firstrun.dpkg-dist").read_text()
    require(defaults.read_text() == original_defaults and original_defaults.count("OPENSSHD_REGENERATE_HOST_KEYS=true") == 1,
            "Armbian 主機金鑰再生成設定不是已驗證的首次預設")
    ssh_defaults = writable_path(root, "etc/default/ssh")
    active_defaults = [line.strip() for line in ssh_defaults.read_text().splitlines() if line.strip() and not line.lstrip().startswith("#")]
    require(active_defaults in (["SSHD_OPTS="], ["SSHD_OPTS=\"\""], ["SSHD_OPTS=''"], []),
            "SSH 啟動參數包含未驗證設定")
    directory = writable_path(root, "etc/ssh")
    configs = [writable_path(root, "etc/ssh/sshd_config")]
    includes = writable_path(root, "etc/ssh/sshd_config.d")
    if includes.exists():
        require(includes.is_dir(), "SSH 設定片段目錄不合法")
        configs.extend(writable_path(root, str(path.relative_to(root))) for path in includes.glob("*.conf"))
    for config in configs:
        for line in config.read_text().splitlines():
            words = line.strip().split()
            if not words or words[0].startswith("#"):
                continue
            require(words[0].lower() not in ("hostkey", "hostkeyagent", "hostcertificate"), "SSH 設定使用未驗證的主機金鑰來源")
            require(words[0].lower() != "include" or words == ["Include", "/etc/ssh/sshd_config.d/*.conf"],
                    "SSH 設定包含未知引入路徑")
    allowed = {"ssh_host_" + kind + "_key" + suffix for kind in ("rsa", "ecdsa", "ed25519") for suffix in ("", ".pub")}
    keys = list(directory.glob("ssh_host_*"))
    for path in keys:
        checked = writable_path(root, str(path.relative_to(root)))
        require(path.name in allowed and checked.is_file() and checked.stat().st_nlink == 1,
                "建置主機金鑰含連結或未知檔案，拒絕清除")
    outputs = {"usr/lib/bpi-k1-native/ssh-hostkeys": (SSH_HOSTKEY_HELPER, 0o755),
               "usr/lib/systemd/system/bpi-k1-ssh-hostkeys.service": (SSH_HOSTKEY_UNIT, 0o644),
               **{"etc/systemd/system/" + name + ".d/10-bpi-k1-hostkeys.conf": (SSH_HOSTKEY_DEPENDENCY, 0o644)
                  for name in SSH_UNIT_CONTRACTS}}
    for relative in outputs:
        require(not writable_path(root, relative).exists(), "SSH 金鑰守門目的地已存在")
    # 全部布局核對後才改動；不掃描或刪除 root／home 的使用者登入資料。
    for relative, (contents, mode) in outputs.items():
        common.write(root / relative, contents, mode)
    defaults.write_text(original_defaults.replace("OPENSSHD_REGENERATE_HOST_KEYS=true", "OPENSSHD_REGENERATE_HOST_KEYS=false"))
    for path in keys:
        path.unlink()
    require(not list(directory.glob("ssh_host_*")), "建置主機金鑰清除不完整")
    return {"status": "configured", "removed_build_key_paths": sorted("/" + str(path.relative_to(root)) for path in keys),
            "guard_service": "/usr/lib/systemd/system/bpi-k1-ssh-hostkeys.service",
            "required_before": ["ssh.service", "ssh.socket"], "upstream_unit_sha256": unit_sources,
            "firstrun_regeneration": False, "subsequent_boot_policy": "preserve-valid-host-key-pairs",
            "hardware_validation": "pending"}


def finish(rootfs, work_dir, run_id):
    root = root_directory(rootfs)
    run_id = build_id(run_id)
    work = root_directory(work_dir)
    state_path = common.regular(work / "stage.json")
    state = json.loads(state_path.read_text())
    require(state.get("schema_version") == 1 and state.get("status") == "staged" and
            state.get("source_kind") == "armbian-native-rootfs", "兩階段紀錄格式不符")
    require(state["identity"]["build_id"] == run_id, "建置識別與 stage 不符")
    require(state["bindings"]["rootfs"] == binding(root), "兩階段必須操作同一個框架根系統")
    require(state["stage_path"] == "/var/tmp/bpi-k1-native-" + run_id, "暫存路徑與建置識別不符")
    target = writable_path(root, state["stage_path"].lstrip("/"))
    token = json.loads(common.regular(target / "transaction.json").read_text())
    require(token == {"stage_sha256": digest(state_path), "build_id": run_id}, "stage 紀錄遭到變更")
    result_path = work / "integration.json"
    if result_path.exists():
        require(json.loads(common.regular(result_path).read_text()).get("status") != "complete",
                "此兩階段整合已完成，拒絕重複執行")
    identity = state["identity"]
    lock_hash = identity["acceleration_lock_sha256"]
    require(digest(work / "acceleration.lock.json") == lock_hash and
            digest(Path(state["bindings"]["lock_path"])) == lock_hash, "來源鎖與 stage 不符")
    for key, path in (("producer_sha256", Path(__file__)), ("acceleration_tool_sha256", Path(acceleration.__file__)),
                      ("common_tool_sha256", Path(common.__file__)),
                      ("gpio_tool_sha256", Path(gpio_packages.__file__)),
                      ("runtime_tool_sha256", REPO / "tools/collect_bpi_k1_runtime.py")):
        require(digest(path) == state["bindings"][key], "兩階段使用的工具版本不同")
    expected_entries = {"transaction.json", *(item["filename"] for item in state["packages"])}
    require({path.name for path in target.iterdir()} == expected_entries, "暫存目錄包含缺少或額外檔案")
    for item in state["packages"]:
        path = common.regular(target / item["filename"])
        require(path.stat().st_size == item["bytes"] and digest(path) == item["sha256"], "stage DEB 內容遭到變更")
    lock = acceleration.load_lock(work / "acceleration.lock.json")
    board = identity["board"]
    armbian_board = identity.get("armbian_board", common.BOARDS[board][0])
    if "armbian_board" in identity:
        require(digest(Path(targets.__file__)) == state["bindings"].get("board_targets_tool_sha256") and
                digest(REPO / targets.REGISTRY) == state["bindings"].get("board_targets_lock_sha256"),
                "兩階段的官方格式板名契約已變更")
    packages = validate_root(root, board, lock, armbian_board)
    current_kernel = kernel_evidence(root, board, lock, packages)
    require(current_kernel == state["kernel"], "APT 安裝期間核心檔案或套件發生未授權變更")
    versions = {(item["package"], item["architecture"]): item["version"] for item in packages}
    for item in state["packages"]:
        require(versions.get((item["package"], item["architecture"])) == item["version"],
                "框架尚未安裝指定 DEB 版本：" + item["package"])
    gpio_verification = gpio_packages.verify_installed(root, identity["cm6_gpio_packages"]) if "cm6_gpio_packages" in identity else None
    require(all(any(row["package"] == name for row in packages) for name in DESKTOP_PACKAGES),
            "框架尚未安裝所有 GNOME 與診斷必要套件")
    if "connectivity_lock_sha256" in identity:
        require(digest(work / "connectivity.lock.json") == identity["connectivity_lock_sha256"] and
                digest(CONNECTIVITY_LOCK) == identity["connectivity_lock_sha256"], "藍牙來源鎖與 stage 不符")
        require(digest(work / "bluetooth-package.json") == identity["cm6_bluetooth_manifest_sha256"],
                "藍牙套件來源紀錄與 stage 不符")
        verify_bluetooth_files(root, json.loads((work / "connectivity.lock.json").read_text()))
    result = {"schema_version": 1, "source_kind": "armbian-native-rootfs", "status": "configuring",
              "identity": identity, "desktop": "gnome-wayland", "kernel": current_kernel,
              "source_packages": state["packages"], "verified_sources": state["verified_sources"],
              "gpio_package_verification": gpio_verification,
              "hardware_validation": "pending", "media_policy_applied": False,
              "limitations": ["Ubuntu 一般依賴依框架 APT 取得，已安裝清單不是完整來源快照。",
                              "來源簽章範圍僅涵蓋已鎖定加速索引；相機及韌體來源缺口仍依各自紀錄。",
                              "本工具不掛載、不編譯核心、不執行 APT，也不宣告硬體驗收通過。"]}
    try:
        result["ssh_host_keys"] = prepare_ssh_host_keys(root)
        result.update(configure(root, board, state, work, packages))
        preflight = acceleration.preflight(lock, board, root, "installed", armbian_board)
        write_json(work / "acceleration-preflight.json", preflight)
        result["preflight"] = preflight
        require(preflight["passed"], "安裝後加速預檢失敗：" + "; ".join(preflight["errors"]))
        inventory = {"schema_version": 1, "count": len(packages), "packages": packages,
                     "dpkg_status_sha256": digest(root_file(root, "var/lib/dpkg/status"))}
        write_json(work / "installed-packages.json", inventory)
        result["installed_packages"] = {"file": "installed-packages.json", "count": len(packages),
                                        "sha256": digest(work / "installed-packages.json")}
        result["desktop_build_runtime"] = cleanup_desktop_runtime(root)
        # 只刪已校驗的本次暫存目錄；不清理框架的 APT 快取或其他 /var/tmp 內容。
        shutil.rmtree(target)
        result["status"] = "complete"
        write_json(result_path, result)
        return result
    except Exception as exc:
        result.update(status="failed", error=str(exc))
        write_json(result_path, result)
        raise


class ChineseParser(argparse.ArgumentParser):
    def __init__(self, *args, **kwargs):
        kwargs["add_help"] = False
        super().__init__(*args, **kwargs)
        self._positionals.title = "位置參數"
        self._optionals.title = "選項"
        self.add_argument("-h", "--help", action="help", help="顯示使用說明並結束")

    def format_help(self):
        return super().format_help().replace("usage: ", "用法：", 1)

    def error(self, message):
        self.exit(2, "參數不完整或無法辨識，請使用 --help 核對。\n")


def main():
    parser = ChineseParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    before = sub.add_parser("stage", help="驗證並暫存套件，輸出框架 APT 安裝參數")
    after = sub.add_parser("finish", help="核對框架安裝結果、配置共用系統並保存證據")
    for item in (before, after):
        item.add_argument("--rootfs", required=True, type=Path, help="Armbian 管理中的根系統目錄")
        item.add_argument("--work-dir", required=True, type=Path, help="根系統以外的兩階段紀錄目錄")
        item.add_argument("--build-id", required=True, help="本次建置識別，不可含斜線")
    before.add_argument("--board", choices=common.BOARDS, required=True)
    before.add_argument("--armbian-board", help="框架的完整板名；省略時僅接受原始板名")
    before.add_argument("--deb-cache", required=True, type=Path)
    before.add_argument("--lock", type=Path, default=DEFAULT_LOCK)
    before.add_argument("--cm6-bluetooth-package", type=Path)
    before.add_argument("--cm6-camera-cache", type=Path)
    before.add_argument("--cm6-gpio-cache", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "stage":
            result = stage(args.board, args.rootfs, args.work_dir, args.build_id, args.deb_cache, args.lock,
                           args.cm6_bluetooth_package, args.cm6_camera_cache, args.armbian_board, args.cm6_gpio_cache)
        else:
            result = finish(args.rootfs, args.work_dir, args.build_id)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (ValueError, OSError, KeyError, acceleration.AuditError, subprocess.SubprocessError) as exc:
        print("原生根系統整合失敗：" + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
