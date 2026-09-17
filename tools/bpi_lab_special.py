#!/usr/bin/env python3
"""六板原配組件與離線引導配置核對；不接硬體、不執行原廠腳本。"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import struct
import tempfile
import zlib

if __package__:
    from . import bpi_lab_amlogic as shared
    from . import bpi_lab_uboot as uboot
else:
    import bpi_lab_amlogic as shared
    import bpi_lab_uboot as uboot


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "bpi-lab-special-v1"
MAX_FILE = 128 * 1024**2
MAX_TOTAL = 512 * 1024**2
MAX_EXPANDED = 128 * 1024**2
UUID = r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}"
RELEASE = r"[0-9]+\.[0-9]+\.[A-Za-z0-9_.+~-]+"
PROFILES = {
    "bpi-f2p": {"artifact": "bananapif2p", "family": "sunplus-sp7021-bpi", "group": "sunplus",
                "arch": "arm32", "dtbs": ["sp7021-bpi-f2p.dtb"],
                "compatible": ["sunplus,sp7021-achip"], "model": "SP7021/CA7/BPI-F2P"},
    "bpi-f2s": {"artifact": "bananapif2s", "family": "sunplus-sp7021-bpi", "group": "sunplus",
                "arch": "arm32", "dtbs": ["sp7021-bpi-f2s.dtb"],
                "compatible": ["sinovoip,bpi-f2s", "sunplus,sp7021-achip"], "model": "Banana Pi BPI-F2S"},
    "bpi-ai2n": {"artifact": "bpi-ai2n", "family": "renesas-rzv2n-bpi", "group": "renesas",
                 "arch": "arm64", "dtbs": ["renesas/bananapi-ai2n.dtb"],
                 "compatible": ["sinovoip,bpi-ai2n", "renesas,r9a09g056n44", "renesas,r9a09g056"],
                 "model": "Bananapi BPI-AI2N", "script": "boot-renesas-rzv2n-bpi.cmd",
                 "script_sha256": "6f4de62ebd99504dca14466694e2447e447aa24801344a1c693a009fa5c533b1"},
    "bpi-m4": {"artifact": "bananapim4", "family": "realtek-rtd139x-bpi", "group": "realtek",
               "arch": "arm64", "dtbs": ["rtd-1395-bananapi-m4-1GB.dtb", "rtd-1395-bananapi-m4-2GB.dtb"],
               "compatible": ["bananapi,bpi-m4", "realtek,rtd1395"], "model": "Banana Pi BPI-M4",
               "env_sha256": "aadf68162e9330c0ff62b350ee659f8f61455b3cf56060bb33d577f7e57ce322"},
    "bpi-w2": {"artifact": "bananapiw2", "family": "realtek-rtd129x-bpi", "group": "realtek",
               "arch": "arm64", "dtbs": ["rtd-1296-bananapi-w2-2GB.dtb"],
               "compatible": ["bananapi,bpi-w2", "realtek,rtd1296"], "model": "Banana Pi BPI-W2",
               "env_sha256": "8ebcc3f08ac0ee853c474aa45d9f2cf4e6c6323182b86db412ea35a8f9ae29a6"},
    "bpi-m6": {"artifact": "bananapim6", "family": "vs680", "group": "synaptics",
               "arch": "arm64", "dtbs": ["synaptics/vs680-a0-bananapi-m6.dtb"],
               "compatible": ["sinovoip,bananapi-m6", "syna,vs680-evk", "syna,vs680"],
               "model": "Banana Pi M6", "script": "boot-vs680.cmd",
               "script_sha256": "9a82b02bd19e194eb82f7c0d8465ec987fcf270e326a3b156f6866e83c9c8ec1"},
}
SUNPLUS_SOURCE = "config/sources/families/include/sunplus_sp7021_bpi_legacy_common.inc"
SUNPLUS_HASH = "229e5268ead36b1ed42546e8d4077fc58cd17b85659f9d8ee8d7d4bb3800b0e2"
REALTEK_SOURCES = {
    "bpi-m4": {"repository": "https://github.com/BPI-SINOVOIP/BPI-M4-bsp",
               "board_pinned_revision": "25f5b88ec4ba34029f964693dc34028b26e6c67c",
               "inspected_local_sha256": {
                   "u-boot-rtk/common/cmd_boot.c": "5fe8584ac1cc6ab04f6525a53c6bdcdb7e4520dba3d12d11cd34005a48a881fe",
                   "u-boot-rtk/common/cmd_bootm.c": "77ba1598b1b25dace521d4779dc50a585931ccd16a0883b1fae2ea792142079c"}},
    "bpi-w2": {"repository": "https://github.com/BPI-SINOVOIP/BPI-W2-bsp",
               "board_pinned_revision": "6e6aefc35dc50b1b8231cdb03a995d088f29eb21",
               "inspected_local_sha256": {
                   "u-boot-rtk/common/cmd_boot.c": "df3b2086ba7032cc60b45c5517836b185f261b18910bf9434e87cfeaceee67ba",
                   "u-boot-rtk/common/cmd_bootm.c": "8b25d22e28b0c27cb9f9b7bd31b76415ac09042b85804bcf4e3ddd732421d086"}},
}


class SpecialError(ValueError):
    """原配資料或離線配置無法核對。"""


def require(condition, message):
    if not condition:
        raise SpecialError(message)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def encoded(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()


def _text(data):
    require(type(data) is bytes and len(data) <= 1024**2, "文字缺失或超限")
    value = data.decode("utf-8")
    require(not any(ord(c) < 32 and c not in "\n\t" for c in value), "文字包含未支援控制字元")
    return value


def assignments(data, *, shell=False):
    """只解析字面賦值；不 source、不 eval、不展開任何指令。"""
    result = {}
    for line in _text(data).splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_]*)=(.*)", line)
        require(match is not None, "設定不是單純賦值")
        key, value = match.groups()
        require(key not in result, "設定鍵重複：" + key)
        if shell:
            require(re.fullmatch(r'''(?:[A-Za-z0-9_./,:=+@%~-]*|'[^'\x00-\x1f]*'|"[^"$`\\\x00-\x1f]*")''', value),
                    "release 含展開或未支援語法")
            values = shlex.split(value)
            value = values[0] if values else ""
        result[key] = value
    return result


def legacy(data, *, kind, arch):
    require(type(data) is bytes and 64 <= len(data) <= MAX_FILE, "legacy 標頭缺失、截斷或超限")
    magic, hcrc, _, size, load, entry, dcrc, os_id, actual_arch, actual_kind, comp, name = struct.unpack(
        ">7I4B32s", data[:64])
    require(magic == 0x27051956 and size == len(data) - 64, "legacy magic 或大小不符")
    require(zlib.crc32(data[:4] + bytes(4) + data[8:64]) == hcrc, "legacy 標頭 CRC 不符")
    require(zlib.crc32(data[64:]) == dcrc, "legacy 內容 CRC 不符")
    require(os_id == 5 and actual_kind == kind and actual_arch in arch, "legacy OS、架構或類型不符")
    require(comp in (0, 1, 6) if kind == 3 else comp == 0, "legacy 壓縮識別未支援")
    payload = data[64:]
    if kind == 6:
        require(len(payload) >= 8, "script 長度表截斷")
        length, end = struct.unpack_from(">II", payload)
        require(end == 0 and length == len(payload) - 8, "script 不是完整單一組件")
        payload = payload[8:]
    return payload, {"load": load, "entry": entry, "arch_id": actual_arch, "compression": comp,
                     "name_hex": name.hex(), "header_crc_verified": True, "data_crc_verified": True}


def _version(data):
    found = set(re.findall(rb"Linux version ([0-9]+\.[0-9]+\.[A-Za-z0-9_.+~-]+)[ \t]", data))
    require(len(found) == 1, "核心內嵌完整版本缺失或歧義；不得使用目錄 0 或截斷的 uImage 名稱")
    return next(iter(found)).decode("ascii")


def validate_kernel(data, *, arch):
    """依實際位元組辨識核心；Realtek 的 uImage 名稱不影響結果。"""
    require(type(data) is bytes and 64 <= len(data) <= MAX_FILE, "核心缺失、截斷或超限")
    if data[:4] == bytes.fromhex("27051956"):
        payload, header = legacy(data, kind=2, arch=(2,) if arch == "arm32" else (22,))
        require(payload[:4] != bytes.fromhex("27051956"), "不接受巢狀核心容器")
        result = validate_kernel(payload, arch=arch)
        require(result["format"] in ("zImage", "Image"), "不接受巢狀核心容器")
        require(header["load"] > 0 and header["entry"] > 0, "legacy 核心載入與入口不可為零")
        require(header["load"] + len(payload) <= 2**32
                and header["load"] <= header["entry"] < header["load"] + len(payload)
                and header["entry"] % 4 == 0, "uImage 入口不在實際載入載荷內、未對齊或載入溢位")
        name = bytes.fromhex(header["name_hex"]).split(b"\0", 1)[0]
        expected = ("Linux-" + result["kernel_release"]).encode()[:32]
        require(name == expected, "uImage 名稱與完整核心版本的截斷前綴不符")
        return {**result, **header, "payload_format": result["format"], "format": "uImage"}
    if arch == "arm64":
        require(data[56:60] == b"ARM\x64", "核心不是 ARM64 Image；未知 vendor 容器不可猜測")
        offset, size, flags = struct.unpack_from("<3Q", data, 8)
        require(len(data) <= size <= MAX_EXPANDED and flags & 1 == 0, "Image 大小或位元組序不符")
        return {"format": "Image", "text_offset": offset, "image_size": size, "flags": flags,
                "kernel_release": _version(data)}
    require(arch == "arm32" and data[36:40] == bytes.fromhex("18286f01"), "核心不是 ARM zImage")
    start, end = struct.unpack_from("<II", data, 40)
    require(end > start and end - start == len(data), "zImage 長度不符")
    # ARM 自解壓 stub 後的 gzip 必須完整解碼；只掃描有界前綴，拒絕多個有效版本。
    positions = [m.start() for m in re.finditer(b"\x1f\x8b\x08", data[:1024**2])]
    require(len(positions) <= 16, "zImage 壓縮入口過多")
    versions, expanded_sizes = set(), []
    for position in positions:
        try:
            decoder = zlib.decompressobj(31)
            expanded = decoder.decompress(data[position:], MAX_EXPANDED + 1)
            require(decoder.eof and len(expanded) <= MAX_EXPANDED, "核心解壓截斷或超限")
            versions.add(_version(expanded))
            expanded_sizes.append(len(expanded))
        except (zlib.error, SpecialError):
            continue
    require(len(versions) == 1 and len(expanded_sizes) == 1, "zImage 無唯一完整 gzip 核心版本；其他壓縮須另適配")
    return {"format": "zImage", "kernel_release": versions.pop(), "image_size": expanded_sizes[0]}


def _cpio_init(data, offset, versions, init):
    """沿用共用結構核對，再讀取已核對項目的 /init 模式；不解出檔案。"""
    end, count = shared._cpio(data, offset, versions)
    while offset < end:
        fields = [int(data[index:index + 8], 16) for index in range(offset + 6, offset + 110, 8)]
        start = offset + 110
        name = data[start:start + fields[11] - 1].decode("utf-8")
        if str(PurePosixPath(name)) == "init":
            init[:] = [(fields[1], fields[4], fields[6])]
        start = (start + fields[11] + 3) & ~3
        offset = (start + fields[6] + 3) & ~3
    return end, count


def validate_initrd(data, *, arch, kernel_release):
    payload, header = legacy(data, kind=3, arch=(2,) if arch == "arm32" else (22,))
    pending, expanded_bytes, entries, segments, versions = payload, 0, 0, 0, set()
    init = []
    while pending:
        pending = pending.lstrip(b"\0")
        if not pending:
            break
        require(segments < 64, "initramfs 串接段過多")
        if pending.startswith((b"070701", b"070702")):
            end, count = _cpio_init(pending, 0, versions, init)
            expanded_bytes += end
            entries += count
            pending = pending[end:]
        else:
            expanded, pending = shared._uncompress(pending, MAX_EXPANDED - expanded_bytes)
            expanded_bytes += len(expanded)
            offset = 0
            while offset < len(expanded):
                if expanded[offset] == 0:
                    offset += 1
                    continue
                offset, count = _cpio_init(expanded, offset, versions, init)
                entries += count
        require(expanded_bytes <= MAX_EXPANDED and entries <= 200000, "initramfs 展開大小或項目超限")
        segments += 1
    require(entries > 0 and versions == {kernel_release}, "initramfs modules 版本缺失或與核心錯配")
    require(bool(init) and init[0][0] & 0o170000 == 0o100000 and init[0][0] & 0o111
            and init[0][1] == 1 and init[0][2] > 0,
            "initramfs 缺少非空可執行一般檔 /init；連結入口尚未適配")
    return {**header, "format": "legacy", "module_releases": sorted(versions), "entries": entries}


def _open_dir(path):
    path = Path(path).absolute()
    require(".." not in path.parts, "路徑不得含上層跳轉")
    fd = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            new = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = new
        return fd
    except BaseException:
        os.close(fd)
        raise


class Capture:
    def __init__(self, read_file, output, manifest):
        self.read_file, self.output, self.manifest = read_file, Path(output).absolute(), manifest
        parent = _open_dir(self.output.parent)
        try:
            require(self.output.name not in ("", ".", ".."), "輸出目錄名稱不符")
            os.mkdir(self.output.name, mode=0o700, dir_fd=parent)
            self.fd = os.open(self.output.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        finally:
            os.close(parent)
        self.cache, self.total = {}, 0

    def save(self, path, data):
        require(re.fullmatch(r"[A-Za-z0-9_.+-]+(?:/[A-Za-z0-9_.+-]+)*", path)
                and not {".", ".."}.intersection(path.split("/")), "證據路徑無效")
        fd = os.dup(self.fd)
        try:
            parts = path.split("/")
            for part in parts[:-1]:
                try:
                    os.mkdir(part, mode=0o700, dir_fd=fd)
                except FileExistsError:
                    pass
                new = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd)
                fd = new
            target = os.open(parts[-1], os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
            with os.fdopen(target, "wb") as stream:
                stream.write(data)
            return {"path": path, "bytes": len(data), "sha256": digest(data)}
        finally:
            os.close(fd)

    def block(self, stage, reason):
        self.manifest["blockers"].append({"stage": stage, "reason": reason})

    def attempt(self, stage, action):
        try:
            return action()
        except (SpecialError, shared.AmlogicError, uboot.UBootError, UnicodeError, OSError, zlib.error) as exc:
            reason = str(exc) if isinstance(exc, (SpecialError, shared.AmlogicError, uboot.UBootError)) else "檔案、編碼或壓縮資料無法核對"
            self.block(stage, reason)
            return None

    def read(self, path, *, required=True, maximum=MAX_FILE):
        require(re.fullmatch(r"/[A-Za-z0-9_.+-]+(?:/[A-Za-z0-9_.+-]+)*", path)
                and not {".", ".."}.intersection(path.split("/")), "映像路徑無效")
        if path in self.cache:
            return self.cache[path]
        record = {"image_path": path}
        self.manifest["reads"].append(record)
        try:
            data = self.read_file(path)
        except FileNotFoundError:
            record["status"] = "absent"
            if required:
                self.block(path, "缺少原配必要檔案")
            data = None
        except (OSError, ValueError):
            record["status"] = "error"
            self.block(path, "唯讀提供者無法讀取；不能視為不存在")
            data = None
        else:
            if type(data) is not bytes or len(data) > maximum or self.total + len(data) > MAX_TOTAL:
                record["status"] = "error"
                self.block(path, "read_file 不是 bytes，或讀取大小超限")
                data = None
            else:
                self.total += len(data)
                record.update(status="captured", **self.save("files" + path, data))
        self.cache[path] = data
        return data


def _sunplus_env(board, uuid):
    source = (ROOT / SUNPLUS_SOURCE).read_bytes()
    require(digest(source) == SUNPLUS_HASH, "Sunplus family 來源漂移，須重新核對腳本")
    block = source.decode().split('cat > "${boot_dir}/uEnv.txt" <<- EOF\n', 1)[1].split("\n\tEOF", 1)[0]
    replacements = {"${vendor_board}": board, "${SUNPLUS_BPI_VENDOR_CHIP:-SP7021}": "SP7021",
                    "${BOOT_FDT_FILE}": PROFILES[board]["dtbs"][0], "${ROOT_PART_UUID}": uuid,
                    "${ROOTFS_TYPE:-ext4}": "ext4", "${SERIALCON:-ttyS0}": "ttyS0"}
    for key, value in replacements.items():
        block = block.replace(key, value)
    return assignments(block.replace("\\${", "${").encode())


def _environment(c, profile):
    m, group = c.manifest, profile["group"]
    path = "/boot/uEnv.txt" if group in ("realtek", "sunplus") else "/boot/armbianEnv.txt"
    env = assignments(c.read(path, maximum=65536))
    m["environment"] = env
    if group == "sunplus":
        root = env.get("root", "")
        require(re.fullmatch("UUID=" + UUID, root), "Sunplus 根識別不是明確 UUID")
        require(env == _sunplus_env(m["board"], root[5:]), "Sunplus uEnv 與已核對原配命令不符")
        m.update(root_uuid=root[5:].lower(), root_target=root, boot_command="bootm")
        m["bootargs_template"] = ("board=${board} console=" + env["console"] + " root=" + root + " "
                                  + env["rootopt"] + " service=linux sdmmc_on=${sdmmc_on} " + env["bootopts"]).split()
    elif group == "realtek":
        without_root = {key: value for key, value in env.items() if key != "root"}
        require(digest(encoded(without_root)) == profile["env_sha256"], "Realtek 原廠 uEnv 命令、位址或 DTB 選擇已變更")
        root = env.get("root", "")
        match = re.fullmatch(r"(UUID=" + UUID + r"|LABEL=BPI-ROOT) rw rootfstype=ext4 rootwait", root)
        require(match is not None, "Realtek 根識別未正規化為 UUID 或受控 BPI-ROOT 標籤")
        target = match[1]
        m.update(root_uuid=target[5:].lower() if target.startswith("UUID=") else None,
                 root_target=target, root_label="BPI-ROOT" if target.startswith("LABEL=") else None,
                 boot_command=env["aboot"].rstrip(";"))
        m["bootargs_template"] = ("board=${board} console=" + env["console"] + " root=" + root
                                  + " fsck.mode=force fsck.repair=yes service=linux sdmmc_on=${sdmmc_on} "
                                  + env["bootopts"]).split()
        nested = c.read(f"/boot/bananapi/{m['board']}/linux/uEnv.txt", maximum=65536)
        require(assignments(nested) == env, "Realtek 根目錄與板型子目錄 uEnv 不一致")
        roots = []
        for line in _text(c.read("/etc/fstab", maximum=65536)).splitlines():
            fields = line.split("#", 1)[0].split()
            if fields and len(fields) >= 2 and fields[1] == "/":
                require(len(fields) == 6 and fields[2] == "ext4", "fstab 根項目格式或檔案系統不符")
                roots.append(fields[0])
        require(len(roots) == 1 and re.fullmatch("UUID=" + UUID + "|LABEL=BPI-ROOT", roots[0]),
                "Realtek 需唯一 UUID／BPI-ROOT 的 fstab 根項目，不猜測 rootfs 分割區")
        if target.startswith("UUID=") and roots[0].startswith("UUID="):
            require(target.lower() == roots[0].lower(), "Realtek uEnv 與 fstab 根 UUID 不同")
        m["root_fstab_target"] = roots[0]
    else:
        allowed = {"rootdev", "rootfstype", "fdtfile", "verbosity", "console", "bootlogo",
                   "docker_optimizations", "extraargs", "bootopts", "overlays", "user_overlays",
                   "overlay_prefix", "debug_uart", "board", "usbstoragequirks", "earlycon"}
        require(set(env) <= allowed, "環境含可改寫引導流程的未知變數")
        root = env.get("rootdev", "")
        require(re.fullmatch("UUID=" + UUID, root), "根識別不是明確 UUID")
        require(env.get("fdtfile", profile["dtbs"][0]) == profile["dtbs"][0], "環境 DTB 與板型不符")
        require(env.get("rootfstype", "ext4") == "ext4", "目前只核對原配 ext4 根")
        require(not any(env.get(key) for key in ("overlays", "user_overlays", "extraargs", "bootopts")),
                "非空 overlay 或額外參數須另行適配，不得忽略")
        require(env.get("bootlogo", "false") == "false" and env.get("console", "both") in ("serial", "both"),
                "此分支未核對圖形啟動畫面或非序列主控台")
        require(env.get("docker_optimizations", "off") in ("off", "on"), "docker 參數無效")
        level = env.get("verbosity", "1")
        require(re.fullmatch(r"[0-8]", level), "verbosity 超出範圍")
        m.update(root_uuid=root[5:].lower(), root_target=root, boot_command="booti")
        if group == "synaptics":
            args = (f"console=ttyS0,115200n8 console=tty1 rootfstype=ext4 root={root} rw rootwait "
                    f"board=bpi-m6 loglevel={level} tz_enable vppta chipid=43111a82aee08964 cma=343932928@1509949440")
        else:
            require(env.get("board") == "bpi-ai2n" and env.get("debug_uart") == "ttySC0", "AI2N 板型或 UART 缺失")
            console = "console=ttySC0,115200" + (" console=tty1" if env.get("console", "both") == "both" else "")
            args = (f"root={root} rootwait rootfstype=ext4 splash=verbose {console} consoleblank=0 loglevel={level} "
                    "fsck.mode=force fsck.repair=yes net.ifnames=0 board=bpi-ai2n ethaddr=${ethaddr} "
                    "eth1addr=${eth1addr} serialno=${serial} systemd.machine_id=${chipid}")
            if env.get("docker_optimizations", "off") == "on":
                args += " cgroup_enable=memory swapaccount=1"
        m["bootargs_template"] = args.split()


def _dtb(data, path, profile):
    result = shared._fdt(data, path, profile)
    model = shared._run(["/usr/bin/fdtget", "-t", "s", str(path), "/", "model"]).decode().strip()
    require(model == profile["model"], "DTB model 與板型不符")
    roots = shared._fdtget(path, "/", mode="l")
    banks, vendor_reserved = [], []
    if profile["group"] == "realtek":
        ac = shared._fdtget(path, "/", "#address-cells")
        sc = shared._fdtget(path, "/", "#size-cells")
        require(ac in (["1"], ["2"]) and sc in (["1"], ["2"]), "Realtek 根 DT cells 不符")
        for node in roots:
            if node == "memory" or node.startswith("memory@"):
                banks.extend(shared._cell_spans(shared._fdtget(path, "/" + node, "reg"), int(ac[0]), int(sc[0])))
        require(banks, "Realtek DT 未明示 DRAM，不能依 DTB 檔名猜容量")
        if "chosen" in roots and "cma-region-info" in shared._fdtget(path, "/chosen", mode="p"):
            cells = shared._fdtget(path, "/chosen", "cma-region-info")
            require(cells and len(cells) % 3 == 0, "Realtek CMA 三元組截斷")
            for index in range(0, len(cells), 3):
                flag, size, start = (int(value, 16) for value in cells[index:index + 3])
                require(flag in (0, 1) and size > 0 and start + size <= 2**32, "Realtek CMA 三元組不符")
                vendor_reserved.append({"start": start, "size": size, "role": "cma"})
        if "rtk,ion" in roots:
            for node in shared._fdtget(path, "/rtk,ion", mode="l"):
                node = "/rtk,ion/" + node
                if "rtk,memory-reserve" not in shared._fdtget(path, node, mode="p"):
                    continue
                cells = shared._fdtget(path, node, "rtk,memory-reserve")
                require(cells and len(cells) % 3 == 0, "Realtek ION 三元組截斷")
                role = "audio" if shared._fdtget(path, node, "reg") == ["8"] else "ion"
                for index in range(0, len(cells), 3):
                    start, size, _ = (int(value, 16) for value in cells[index:index + 3])
                    require(size > 0 and start + size <= 2**32, "Realtek ION 保留區不符")
                    vendor_reserved.append({"start": start, "size": size, "role": role})
    return {**result, "model": model, "banks": banks, "vendor_reservations": vendor_reserved}


def _prepare(c, requested):
    m, p = c.manifest, PROFILES[c.manifest["board"]]
    platform = json.loads((ROOT / "config/bpi-lab/platforms.json").read_bytes())
    rows = [row for row in platform["boards"] if row["board"] == m["board"]]
    require(len(rows) == 1 and rows[0]["group"] == p["group"] and rows[0]["family"] == p["family"]
            and rows[0]["artifact_board"] == p["artifact"] and rows[0]["architecture"] == p["arch"],
            "platforms 板型或家族映射不符")
    for path in rows[0]["sources"]:
        require(isinstance(path, str) and re.fullmatch(r"[A-Za-z0-9_.+-]+(?:/[A-Za-z0-9_.+-]+)*", path)
                and not {".", ".."}.intersection(path.split("/")), "platforms 來源路徑不安全")
        data = shared._read_evidence(ROOT / path, 1024**2)
        m["sources"].append({"source_path": path, **c.save("sources/" + path, data)})
    rel = assignments(c.read("/etc/armbian-release", maximum=65536), shell=True)
    m["release"] = rel
    require(rel.get("BOARD") == p["artifact"] and rel.get("BOARDFAMILY") == p["family"], "原配 release 板型或家族錯配")
    if "ARCH" in rel:
        # 原配 F2P release 使用核心架構 arm；建置套件的 armhf 亦指 ARM32。
        release_arch = {"arm": "arm32", "armhf": "arm32", "arm64": "arm64"}.get(rel["ARCH"])
        require(release_arch == p["arch"], "release 架構錯配")
    c.attempt("environment", lambda: _environment(c, p))
    vendor = p["group"] in ("sunplus", "realtek")
    base = f"/boot/bananapi/{m['board']}/linux/" if vendor else "/boot/"
    kernel_path = base + ("uImage" if vendor else "Image")
    kernel = c.read(kernel_path)
    checked = c.attempt("kernel", lambda: validate_kernel(kernel, arch=p["arch"]))
    if checked:
        m["components"]["kernel"] = {**checked, **c.save("artifacts/kernel", kernel), "image_path": kernel_path}
        actual = checked["kernel_release"]
        m["kernel_release"] = actual
        if requested != actual and not (requested == "0" and p["group"] == "sunplus"):
            c.block("kernel_release", "指定版本與核心內嵌完整版本不同")
        for key in ("KERNEL_VERSION", "KERNEL_RELEASE", "KERNEL"):
            if key in rel and rel[key] not in (actual, "0"):
                c.block("kernel_release", "原配 release 與核心版本不同：" + key)
        for suffix in ("include/config/kernel.release", "include/generated/utsrelease.h"):
            path = f"/usr/src/linux-headers-{actual}/{suffix}"
            header = c.read(path, required=False, maximum=65536)
            if header is not None:
                expected = actual if suffix.endswith("kernel.release") else f'#define UTS_RELEASE "{actual}"'
                if _text(header).strip() != expected:
                    c.block("kernel_header", "原配 headers 與核心內嵌版本不同")
        m["release_evidence"] = {"binary": actual, "requested": requested,
                                 "zero_resolved": requested == "0", "directory_name_trusted": False}
        if p["group"] == "sunplus" and checked["format"] != "uImage":
            c.block("kernel", "Sunplus aboot=bootm 需要 legacy uImage；zImage fallback 不可冒充")
        if p["group"] in ("renesas", "synaptics") and checked["format"] != "Image":
            c.block("kernel", "原配 booti 需要 raw ARM64 Image")
        initrd_path = base + "uInitrd"
        initrd = c.read(initrd_path)
        metadata = c.attempt("initrd", lambda: validate_initrd(initrd, arch=p["arch"], kernel_release=actual))
        if metadata:
            m["components"]["initrd"] = {**metadata, **c.save("artifacts/initrd", initrd), "image_path": initrd_path}
    for index, name in enumerate(p["dtbs"]):
        path = base + ("" if vendor else "dtb/") + name
        data = c.read(path, maximum=8 * 1024**2)
        if data is not None:
            result = c.attempt("dtb", lambda: _dtb(data, c.output / ("files" + path), p))
            if result:
                if p["group"] == "realtek":
                    capacity = 0x40000000 if name.endswith("-1GB.dtb") else 0x80000000
                    if result["banks"] != [{"start": 0, "size": capacity}]:
                        c.block("dtb", "原配 DRAM 選擇檔案與 DT memory 不一致")
                role = "dtb" if len(p["dtbs"]) == 1 else "dtb_" + str(index)
                m["components"][role] = {**result, **c.save("artifacts/" + role, data), "image_path": path}
    if p["group"] == "realtek":
        data = c.read(base + "bluecore.audio")
        if data:
            m["components"]["audio"] = {**c.save("artifacts/audio", data), "format": "opaque-vendor",
                                         "image_path": base + "bluecore.audio", "validated": False}
        else:
            c.block("audio", "缺少非空原配音訊韌體")
    if p["group"] == "renesas":
        for role, name in (("opencva", "OpenCV_Bin.bin"), ("codec", "Codec_Bin.bin")):
            data = c.read("/boot/" + name)
            if data:
                m["components"][role] = {**c.save("artifacts/" + role, data), "format": "opaque-vendor",
                                         "image_path": "/boot/" + name, "validated": False}
            else:
                c.block(role, "原配腳本的必要額外載荷缺失或為空")
    if "script" in p:
        cmd = c.read("/boot/boot.cmd", maximum=1024**2)
        scr = c.read("/boot/boot.scr", maximum=1024**2)
        def script_check():
            payload, header = legacy(scr, kind=6, arch=(2, 22))
            require(payload == cmd and digest(payload) == p["script_sha256"], "boot.scr、boot.cmd 與已核定來源不一致")
            m["boot_script"] = header
        c.attempt("boot_script", script_check)
    for path in ("/boot/extlinux/extlinux.conf", "/boot/boot.ini", "/boot/boot.scr.uimg", "/boot/boot.scr.local"):
        if c.read(path, required=False, maximum=1024**2) is not None:
            c.block("alternate_entry", "替代引導入口優先序未核定：" + path)


def prepare(read_file, *, board, kernel_release, output):
    """擷取原配檔案並保存 manifest；資料阻擋也回傳，API／輸出錯誤才拋例外。

    read_file 必須回傳 bytes，只有 FileNotFoundError 表示不存在。映像摘要、
    分割區與 symlink 安全由唯讀提供者負責；Sunplus 的字串 0 僅請求解析真版本。
    """
    aliases = {p["artifact"]: name for name, p in PROFILES.items()}
    require(isinstance(board, str), "板型必須是字串")
    board = aliases.get(board, board)
    require(callable(read_file) and isinstance(board, str) and board in PROFILES, "必須提供唯讀介面與六板之一")
    require(isinstance(kernel_release, str) and len(kernel_release) <= 128
            and (re.fullmatch(RELEASE, kernel_release) or kernel_release == "0"),
            "核心版本須為完整版本，或 Sunplus 的待解析標記 0")
    p = PROFILES[board]
    m = {"schema": SCHEMA, "board": board, "arch": p["arch"], "family": p["family"],
         "kernel_release": None, "requested_kernel_release": kernel_release, "status": "blocked",
         "components_available": False, "hardware_validated": False, "source_image_verified": False,
         "bootconfig_supported": True, "boot_config_validated": False,
         "execution_ready": False, "root_uuid": None, "root_target": None, "root_label": None,
         "components": {}, "sources": [], "reads": [], "blockers": [],
         "qualification_blockers": ["原廠前置鏈、保護載荷、RAM、媒體、板修訂與復原須另行實板資格核定"],
         "scope": "原配組件與離線配置；不是部署、救援、實板測試或發布授權"}
    if p["group"] == "realtek":
        m["vendor_source_contract"] = copy.deepcopy(REALTEK_SOURCES[board])
        m["qualification_blockers"].append("go all／gosd 已有離線配方；音訊處理器、IPC、PMIC、安全模式及原廠 DT 修改仍須實板核定")
    c = Capture(read_file, output, m)
    try:
        c.attempt("prepare", lambda: _prepare(c, kernel_release))
        required = {"kernel", "initrd"} | ({"dtb_0", "dtb_1"} if board == "bpi-m4" else {"dtb"})
        if not required <= m["components"].keys():
            c.block("incomplete", "必要組件核對未全部完成")
        if not m["blockers"]:
            m.update(status="prepared", components_available=True)
        c.save("manifest.json", encoded(m))
        return m
    finally:
        os.close(c.fd)


def validate(output):
    """從保存的讀取證據重新解析；不信任手改 manifest 的狀態或派生欄位。"""
    output = Path(output).absolute()
    raw = shared._read_evidence(output / "manifest.json", 2 * 1024**2)
    m = json.loads(raw)
    require(m.get("schema") == SCHEMA and m.get("hardware_validated") is False, "manifest schema 或硬體聲明不符")
    records = {}
    for record in m["reads"]:
        path = record["image_path"]
        require(path not in records, "讀取證據重複")
        records[path] = record
    def replay(path):
        require(path in records, "缺少讀取證據：" + path)
        record = records[path]
        if record["status"] == "absent":
            raise FileNotFoundError(path)
        require(record["status"] == "captured", "原讀取失敗，不能重播成成功")
        data = shared._read_evidence(output / ("files" + path), MAX_FILE)
        require(record["bytes"] == len(data) and record["sha256"] == digest(data), "原配證據摘要已變更")
        return data
    with tempfile.TemporaryDirectory(prefix="bpi-special-verify-") as temp:
        checked = prepare(replay, board=m["board"], kernel_release=m["requested_kernel_release"], output=Path(temp) / "replay")
        require(checked["status"] == "prepared", "證據重播仍有具體阻擋")
        require(encoded(checked) == raw, "manifest 派生欄位或本機來源已變更")
        for record in checked["sources"] + list(checked["components"].values()):
            data = shared._read_evidence(output / record["path"], MAX_FILE)
            require(len(data) == record["bytes"] and digest(data) == record["sha256"], "來源或派生組件已變更")
    return checked


def _realtek_recipe(m, t):
    """原廠 go all／gosd 的軟體配方，保留原命令與硬編碼媒體限制。"""
    require(set(t) == {"board", "kernel_release", "source", "ram", "addresses", "bindings", "vendor"},
            "Realtek 範本需額外明示 vendor 條件")
    vendor = t.pop("vendor")
    require(type(vendor) is dict and set(vendor) == {"dram_size", "secure_mode", "hyp_loadaddr", "root_identity"},
            "Realtek 原廠執行條件欄位不完整")
    require(vendor["secure_mode"] == "non-secure" and vendor["hyp_loadaddr"] == "", "安全引導或 HYP 分支尚未適配")
    require(vendor["dram_size"] in (("1GB", "2GB") if m["board"] == "bpi-m4" else ("2GB",)), "Realtek DRAM 分支未知")
    source = t["source"]
    require(type(source) is dict and set(source) == {"type", "device", "partition", "partuuid", "filesystem", "identity_sha256"},
            "原廠 FAT 來源需明示分割區與唯讀檢查證據摘要")
    require(source["type"] == "vendor-fat" and source["device"] in ("sd", "mmc")
            and source["partition"] == "0:1" and source["filesystem"] == "vfat", "原廠入口限已核對的 FAT 0:1，不能改走 TFTP")
    require(re.fullmatch(r"(?:[0-9a-f]{8}-[0-9a-f]{2}|[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})", source["partuuid"]),
            "FAT 來源 PARTUUID 無效")
    require(isinstance(source["identity_sha256"], str) and re.fullmatch(r"[0-9a-f]{64}", source["identity_sha256"]),
            "需外部 DiskReader 身分核對證據摘要")
    identity = vendor["root_identity"]
    require(type(identity) is dict and set(identity) == {"uuid", "label", "filesystem", "evidence_sha256"}, "根檔案系統身分欄位不符")
    require(re.fullmatch(UUID, identity["uuid"]) and identity["label"] == "BPI-ROOT" and identity["filesystem"] == "ext4"
            and re.fullmatch(r"[0-9a-f]{64}", identity["evidence_sha256"]), "根身分須由外部檢查證據明示 UUID、標籤與 ext4")
    for target in (m["root_target"], m["root_fstab_target"]):
        require(target == "LABEL=" + identity["label"] or target.lower() == "uuid=" + identity["uuid"].lower(),
                "外部根身分與原配 uEnv／fstab 不符")
    components = dict(m["components"])
    if m["board"] == "bpi-m4":
        selected = "dtb_0" if vendor["dram_size"] == "1GB" else "dtb_1"
        components["dtb"] = components[selected]
        del components["dtb_0"], components["dtb_1"]
    require(components["kernel"]["format"] == "Image", "原廠 ARM64 rtk_call_booti 只接受實際 raw Image")
    env = m["environment"]
    expected = {"kernel": int(env["kernel_loadaddr"], 16), "initrd": int(env["rootfs_loadaddr"], 16),
                "dtb": int(env["fdt_loadaddr"], 16), "audio": int(env["audio_loadaddr"], 16)}
    require(t["addresses"] == expected, "Realtek 位址不得偏離原配 uEnv")
    size = 0x40000000 if vendor["dram_size"] == "1GB" else 0x80000000
    require(components["dtb"]["banks"] == [{"start": 0, "size": size}], "DRAM 綁定與實際 DT memory 不符")
    require(t["ram"]["banks"] == components["dtb"]["banks"], "範本 DRAM bank 與原 DT 不同")
    # 原廠 booti_setup 將 Image 搬至 bi_dram[0].start + text_offset。
    destination = components["kernel"]["text_offset"]
    require(destination < 2**32, "原廠 text_offset 僅處理低 32 位元")
    return components, vendor, {"start": destination, "size": components["kernel"]["image_size"]}


def bootconfig(output, *, template):
    """產生有界離線載入配方；不是共用 U-Boot 執行器的 ABI 或硬體資格。"""
    m = validate(output)
    t = copy.deepcopy(template)
    realtek = m["board"] in ("bpi-m4", "bpi-w2")
    require(type(t) is dict and set(t) == {"board", "kernel_release", "source", "ram", "addresses", "bindings"} | ({"vendor"} if realtek else set()),
            "範本須完整明示板型、版本、來源、RAM、位址與原環境綁定")
    require(t["board"] == m["board"] and t["kernel_release"] == m["kernel_release"], "範本板型或核心版本錯配")
    vendor, relocation = None, None
    if realtek:
        components, vendor, relocation = _realtek_recipe(m, t)
    else:
        components = m["components"]
        uboot._source(t["source"])
    bits = 32 if m["arch"] == "arm32" else 64
    ram = t["ram"]
    require(type(ram) is dict and set(ram) == {"banks", "reserved", "kernel_work"}, "RAM 欄位不符")
    for kind in ("banks", "reserved"):
        require(type(ram[kind]) is list and 1 <= len(ram[kind]) <= 64, "RAM bank 與保護區須明示")
        for span in ram[kind]:
            uboot._span(span, bits)
        uboot._disjoint(ram[kind], "RAM " + kind)
    work = uboot._span(ram["kernel_work"], bits)
    require(any(uboot._contains(bank, work) for bank in ram["banks"]), "核心工作區越界")
    require(type(t["addresses"]) is dict and set(t["addresses"]) == set(components), "每個必要載荷均須明示位址")
    slots, loads = [], []
    for role, component in components.items():
        address = uboot._address(t["addresses"][role], bits)
        t["addresses"][role] = address
        require(role != "dtb" or address % 8 == 0, "DTB 載入位址未對齊")
        size = component["bytes"] + (65536 if role == "dtb" else 0)
        if role == "kernel" and component["format"] == "Image":
            size = max(size, component["image_size"])
        span = {"start": address, "size": size}
        require(any(uboot._contains(bank, span) for bank in ram["banks"]), "載荷超出 RAM：" + role)
        loads.append({"role": role, "address": address, **component})
        if role == "kernel" and not realtek:
            require(uboot._contains(work, span), "核心載入超出工作區")
        else:
            slots.append(span)
    slots.append(work)
    uboot._disjoint(slots, "載荷與核心工作區")
    dtb = components["dtb"]
    require(not dtb["dynamic_cma"], "動態 CMA 尚未核定為具體保護範圍")
    protected = ram["reserved"] + dtb["reservations"]
    if realtek:
        require(uboot._contains(work, relocation), "Realtek Image 搬移目的未包含於核心工作區")
        protected += [{"start": 0x2000, "size": 0x1000},
                      {"start": 0x2f000 if m["board"] == "bpi-m4" else 0x1f000, "size": 0x1000}]
        protected += [{"start": span["start"], "size": span["size"]}
                      for span in dtb["vendor_reservations"] if span["role"] != "audio"]
    if m["board"] == "bpi-m6":
        protected.append({"start": 1509949440, "size": 343932928})
        for role, address in {"kernel": 0x04a80000, "initrd": 0x0ca00000, "dtb": 0x15a00000}.items():
            require(t["addresses"][role] == address, "M6 腳本固定載入位址不得改寫")
    # Renesas 額外韌體只能載入其原配 DT 保留區；不得借此豁免其他載荷。
    firmware_areas = {"opencva": {"start": 0xa8000000, "size": 0x7cff000},
                      "codec": {"start": 0xafd00000, "size": 0x300000}}
    for load in loads:
        role = load["role"]
        span = work if role == "kernel" else {"start": load["address"], "size": load["bytes"] + (65536 if role == "dtb" else 0)}
        permitted = firmware_areas.get(role)
        if permitted:
            require(load["address"] == permitted["start"] and uboot._contains(permitted, span)
                    and permitted in dtb["reservations"], "Renesas 額外韌體未配對原 DT 保留區")
        for area in dtb["vendor_reservations"]:
            if area["role"] == "audio":
                require(not uboot._overlap(span, area) or role == "audio" and uboot._contains(area, span),
                        "Realtek 音訊保留區與載荷不符")
        require(not any(uboot._overlap(span, area) and area != permitted for area in protected), "載荷撞到保護區：" + role)
        if realtek and role == "kernel":
            source_span = {"start": load["address"], "size": max(load["bytes"], load["image_size"])}
            require(not any(uboot._overlap(source_span, area) for area in protected), "Realtek 核心原始載入區撞到保護區")
    kernel = components["kernel"]
    if kernel["format"] == "uImage":
        for address in (kernel["load"], kernel["entry"]):
            require(uboot._contains(work, {"start": address, "size": kernel["image_size"]}), "uImage 解壓目的或入口超出工作區")
    elif not realtek:
        require(kernel["format"] == "Image", "未適配的核心搬移格式")
        base = t["addresses"]["kernel"] - kernel["text_offset"] if kernel["flags"] & 8 else ram["banks"][0]["start"]
        require(base >= 0, "Image text_offset 下溢")
        destination = (base + 0x1fffff) // 0x200000 * 0x200000 + kernel["text_offset"]
        relocation = uboot._span({"start": destination, "size": kernel["image_size"]}, bits)
        require(uboot._contains(work, relocation)
                and any(uboot._contains(bank, relocation) for bank in ram["banks"]), "Image 實際搬移目的超出工作區或 RAM")
    args = " ".join(m["bootargs_template"])
    names = set(re.findall(r"\$\{([a-z0-9_]+)\}", args))
    if m["board"] == "bpi-ai2n":
        names.update(("ocaaddr", "codaddr", "ocabin", "codbin"))
    bindings = t["bindings"]
    require(type(bindings) is dict and set(bindings) == names, "原廠環境綁定缺失或多餘")
    if m["board"] == "bpi-ai2n":
        for address_key, file_key, role in (("ocaaddr", "ocabin", "opencva"), ("codaddr", "codbin", "codec")):
            require(bindings[file_key] == Path(components[role]["image_path"]).name
                    and uboot._address(bindings[address_key], bits) == t["addresses"][role],
                    "AI2N 原配額外載荷環境與位址不一致")
    for name, value in bindings.items():
        require(isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.:+-]{1,128}", value), "環境綁定含指令或無效字元")
        if name == "board":
            require(value == m["board"], "板型綁定不符")
        if name == "sdmmc_on":
            require(value in ("0", "1"), "sdmmc_on 必須來自原配環境的 0 或 1")
        args = args.replace("${" + name + "}", value)
    command = m["boot_command"] if realtek else m["boot_command"] + " " + " ".join(f"{t['addresses'][role]:x}" for role in ("kernel", "initrd", "dtb"))
    steps = []
    vendor_environment = None
    if realtek:
        vendor_environment = {**m["environment"], **bindings, "device": t["source"]["device"],
                              "partition": t["source"]["partition"], "dram_size": vendor["dram_size"],
                              "hyp_loadaddr": ""}
        steps.append("run abootargs")
        if m["board"] == "bpi-m4":
            steps += ["run " + name for name in ("aload_dtb", "aload_kernel", "aload_rootfs", "aload_audio")]
        steps.append(command)
    return {"schema": "bpi-lab-special-bootconfig-v1", "board": m["board"], "kernel_release": m["kernel_release"],
            "source": t["source"], "ram": ram, "loads": loads, "bootargs": args.split(), "boot_command": command,
            "root_uuid": m["root_uuid"], "status": "validated_offline", "boot_config_validated": True,
            "hardware_validated": False, "execution_ready": False, "executed": False,
            "vendor": vendor, "kernel_relocation": relocation, "vendor_steps": steps,
            "vendor_environment": vendor_environment,
            "unset_environment": ["hyp_loadaddr"] if realtek else [],
            "source_identity_verified": False,
            "qualification_blockers": m["qualification_blockers"]}


validate_boot_config = bootconfig


def main(argv=None):
    parser = argparse.ArgumentParser(description="六板原配組件與離線引導配置核對；不操作硬體")
    sub = parser.add_subparsers(dest="command", required=True)
    prep = sub.add_parser("prepare", help="從既有唯讀檔案樹擷取原配組件")
    prep.add_argument("--root", required=True, help="既有檔案樹；不解壓整張映像")
    prep.add_argument("--board", required=True, choices=sorted(PROFILES))
    prep.add_argument("--kernel-release", required=True, help="完整版本；Sunplus 可填 0 要求解析")
    prep.add_argument("--output", required=True, help="不得已存在的輸出目錄")
    for command in ("validate", "bootconfig"):
        item = sub.add_parser(command, help="重播離線證據" if command == "validate" else "核對明示範本")
        item.add_argument("--output", required=True, help="既有證據目錄")
        if command == "bootconfig":
            item.add_argument("--template", required=True, help="離線 RAM 與原配環境綁定 JSON")
    args = parser.parse_args(argv)
    try:
        if args.command == "prepare":
            root = Path(args.root).absolute()
            require(not Path(args.output).absolute().is_relative_to(root), "輸出不得位於唯讀來源內")
            result = prepare(lambda path: shared._read_evidence(root / path.lstrip("/"), MAX_FILE),
                             board=args.board, kernel_release=args.kernel_release, output=args.output)
        elif args.command == "validate":
            result = validate(args.output)
        else:
            result = bootconfig(args.output, template=json.loads(shared._read_evidence(Path(args.template), 1024**2)))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["status"] != "blocked" else 2
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(json.dumps({"status": "blocked", "reason": str(exc), "hardware_validated": False}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
