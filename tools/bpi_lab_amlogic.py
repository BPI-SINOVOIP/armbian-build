#!/usr/bin/env python3
"""Amlogic 原配組件的唯讀擷取與離線準備；不執行映像腳本或操作硬體。"""

from __future__ import annotations

import copy
import hashlib
import json
import lzma
import os
from pathlib import Path
import re
import shlex
import stat
import struct
import subprocess
import tempfile
import zlib

if __package__:
    from . import bpi_lab_uboot as uboot
else:
    import bpi_lab_uboot as uboot


SCHEMA = "bpi-lab-amlogic-v1"
ROOT = Path(__file__).resolve().parents[1]
MAX_BLOB = 256 * 1024**2
MAX_UNPACKED = 512 * 1024**2
MAX_TOTAL = 768 * 1024**2
SCRIPT = "config/bootscripts/boot-meson64.cmd"
NOOP_FIXUP = "patch/kernel/archive/meson64-6.18/overlay/meson-fixup.scr-cmd"
SOURCE_HASHES = {
    NOOP_FIXUP: "e4784db7d5e18b0a7e3365ebe10ef9ebae9cd3424f57adba3019b49f7d137bbf",
    SCRIPT: "21498dc486ce85dd8264dd83ff315fc127b3ff9d37a53f286915c1de8c61bbc1",
    "config/sources/families/include/meson64_common.inc":
        "1d432c533a5d2fd8129a3a7d54093f90c9097a601264bda50ecb3236f7f1b580",
    "config/sources/families/meson-g12b.conf":
        "d7a1af777835af44add6ab32356fcf2e602735fbee1a08764882d3c9f55fc031",
    "config/sources/families/meson-sm1.conf":
        "7469dd45a53ce22b9f6a28dc83ffd66944a1068e8ac133207150c9e81fa781cd",
    "config/boards/bananapicm4io.conf":
        "5974c6a89d06f8228049a4d944c3cce74e36c93375e4c8d7ab71a54a79158c0e",
    "config/boards/bananapim2pro.csc":
        "b0812390ddeadcdb1f968e5a295cf686b8cf6982f4dbc4cf854b9a069002d07e",
    "config/boards/bananapim2s.conf":
        "c77da4942fc0ebc81cad2f57a6afe0fa0ef0b445936e375e83a341826bc35938",
    "config/boards/bananapim5.conf":
        "f61f49681583dc60ac45ed58deb2797809829d0cd11a28ac36b814362686e36a",
    "config/bootenv/meson.txt":
        "1d6b80d348a2fe0d860a3f224d4b1569b2a93fcd7f55179fa083b453cec1e4f6",
}
PROFILES = {
    "bananapicm4io": {
        "source": "config/boards/bananapicm4io.conf", "family": "meson-g12b",
        "dtb": "amlogic/meson-g12b-bananapi-cm4-cm4io.dtb",
        "compatible": ["bananapi,bpi-cm4io", "bananapi,bpi-cm4", "amlogic,a311d", "amlogic,g12b"],
    },
    "bananapim2pro": {
        "source": "config/boards/bananapim2pro.csc", "family": "meson-sm1",
        "dtb": "amlogic/meson-sm1-bananapi-m2-pro.dtb",
        "compatible": ["bananapi,bpi-m2-pro", "amlogic,sm1"],
    },
    "bananapim2s": {
        "source": "config/boards/bananapim2s.conf", "family": "meson-g12b",
        "dtb": "amlogic/meson-g12b-a311d-bananapi-m2s.dtb",
        "compatible": ["bananapi,bpi-m2s", "amlogic,a311d", "amlogic,g12b"],
    },
    "bananapim5": {
        "source": "config/boards/bananapim5.conf", "family": "meson-sm1",
        "dtb": "amlogic/meson-sm1-bananapi-m5.dtb",
        "compatible": ["bananapi,bpi-m5", "amlogic,sm1"],
    },
}
ENV_KEYS = set("rootdev rootfstype verbosity console bootlogo docker_optimizations disable_vu7 "
               "fdtfile overlay_prefix overlays user_overlays usbstoragequirks extraargs extraboardargs".split())
ALTERNATE_ENTRIES = (
    "/boot/boot.ini", "/boot/extlinux/extlinux.conf", "/extlinux/extlinux.conf",
    "/boot/uEnv.txt", "/boot/aml_autoscript", "/boot/s905_autoscript",
    "/boot/customscript.sh", "/boot/customscript.scr", "/boot/boot.scr.uimg",
    "/boot.scr", "/boot.cmd", "/armbianEnv.txt", "/boot.ini", "/uEnv.txt",
    "/aml_autoscript", "/s905_autoscript",
)


class AmlogicError(ValueError):
    """來源、組件或核定配置不符合此適配器的限定契約。"""


class ToolError(AmlogicError):
    """本機工具未完成，不等於來源資料已確定不支援。"""


def _require(condition, reason):
    if not condition:
        raise AmlogicError(reason)


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _match(pattern, value):
    return isinstance(value, str) and re.fullmatch(pattern, value) is not None


def _image_path(path):
    _require(_match(r"/[A-Za-z0-9_.+-]+(?:/[A-Za-z0-9_.+-]+)*", path)
             and all(part not in (".", "..") for part in path.split("/")[1:]), "映像路徑不安全")
    return path


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def _json(data):
    return (json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


class _Capture:
    def __init__(self, read_file, output, board, release):
        self.read_file, self.output = read_file, output
        self.data, self.total = {}, 0
        self.manifest = {
            "schema": SCHEMA, "board": board, "kernel_release": release,
            "status": "preparing", "hardware_validated": False,
            "boot_config_validated": False, "root_uuid": None, "components_available": False,
            "files": [], "reads": [], "sources": [],
            "blockers": [], "components": {}, "overlays": [], "fixups": [],
        }

    def block(self, stage, reason):
        self.manifest["blockers"].append({"stage": stage, "reason": reason})

    def attempt(self, stage, action):
        try:
            return action()
        except ToolError as exc:
            self.manifest["blockers"].append({"stage": stage, "reason": str(exc), "code": "execution_failed"})
        except AmlogicError as exc:
            self.block(stage, str(exc))
        except (OSError, subprocess.SubprocessError):
            self.manifest["blockers"].append({"stage": stage, "code": "execution_failed",
                                              "reason": "本機工具或檔案操作失敗／逾時，未完成核對"})
        return None

    def save(self, relative, data, role, **metadata):
        _write(self.output / relative, data)
        record = {"path": relative, "bytes": len(data), "sha256": _sha(data), "role": role, **metadata}
        self.manifest["files"].append(record)
        return record

    def read(self, path, *, required=False, maximum=MAX_BLOB):
        _image_path(path)
        if path in self.data:
            return self.data[path]
        record = {"image_path": path}
        self.manifest["reads"].append(record)
        try:
            data = self.read_file(path)
        except FileNotFoundError:
            record["status"] = "absent"
            if required:
                self.block(path, "原配必要檔案不存在")
            data = None
        except (OSError, ValueError):
            record["status"] = "error"
            self.block(path, "唯讀提供者拒絕或無法讀取；不能當作不存在")
            data = None
        else:
            if type(data) is not bytes:
                record["status"] = "error"
                self.block(path, "read_file 必須回傳 bytes")
                data = None
            else:
                record.update(bytes=len(data), sha256=_sha(data))
                if len(data) > maximum or self.total + len(data) > MAX_TOTAL:
                    record["status"] = "over-limit"
                    self.block(path, "檔案或累計大小超限；只保存大小及摘要")
                    data = None
                else:
                    self.total += len(data)
                    evidence = self.save("files" + path, data, "image", image_path=path)
                    record.update(status="captured", path=evidence["path"])
        self.data[path] = data
        return data

    def finish(self):
        self.manifest["status"] = "blocked" if self.manifest["blockers"] else "prepared"
        self.manifest["components_available"] = self.manifest["status"] == "prepared"
        _write(self.output / "manifest.json", _json(self.manifest))
        return self.manifest


def _assignments(data, capture, *, shell=False):
    values = {}
    if data is None:
        return values
    try:
        text = data.decode("utf-8")
    except UnicodeError as exc:
        raise AmlogicError("設定不是有效 UTF-8") from exc
    _require("\0" not in text and "\r" not in text, "設定含未支援控制字元")
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_]*)=(.*)", line)
        if match is None:
            capture.block("environment", f"第 {number} 行不是單純賦值；不執行或猜測")
            continue
        key, value = match.groups()
        if key in values:
            capture.block("environment", "設定鍵重複：" + key)
        if shell:
            try:
                # 只接受完整單一字面值；引號內的分號不是 shell 指令分隔符。
                _require(_match(r'''(?:[A-Za-z0-9_./,:=+@%~-]*|'[^'\x00-\x1f]*'|"[^"$`\\\x00-\x1f]*")''', value),
                         "release 含展開、指令語法或未支援引號")
                parts = shlex.split(value, comments=False, posix=True)
            except ValueError:
                capture.block("release", f"第 {number} 行 {key} 含展開、指令語法或未支援引號")
                continue
            value = parts[0] if parts else ""
        values[key] = value
    return values


def _legacy(data, *, kind):
    _require(len(data) >= 64, "legacy 標頭截斷")
    magic, hcrc, _, size, load, entry, dcrc, os_id, arch, actual, comp, name = struct.unpack(">7I4B32s", data[:64])
    _require(magic == 0x27051956 and size == len(data) - 64, "legacy magic 或長度不符")
    _require(zlib.crc32(data[:4] + bytes(4) + data[8:64]) == hcrc, "legacy 標頭 CRC 不符")
    _require(zlib.crc32(data[64:]) == dcrc, "legacy 內容 CRC 不符")
    _require(os_id == 5 and actual == kind and arch in ((2, 22) if kind == 6 else (22,)),
             "legacy OS、架構或類型不符")
    _require(comp == 0 if kind == 6 else comp in (0, 1, 6), "legacy 壓縮識別未支援")
    payload = data[64:]
    if kind == 6:
        _require(len(payload) >= 8, "script 長度表截斷")
        length, end = struct.unpack_from(">II", payload)
        _require(end == 0 and length == len(payload) - 8, "只支援完整單一 script 元件")
        payload = payload[8:]
    return payload, {"arch": arch, "compression": comp, "load": load, "entry": entry,
                     "name_hex": name.hex(), "header_crc_verified": True, "data_crc_verified": True}


def _kernel(data, release):
    _require(len(data) >= 64 and data[56:60] == b"ARM\x64", "核心不是 ARM64 raw Image")
    offset, size, flags = struct.unpack_from("<3Q", data, 8)
    _require(len(data) <= size <= MAX_UNPACKED and not flags & 1, "Image 大小或位元組序不支援")
    versions = set(re.findall(rb"Linux version ([A-Za-z0-9_.+~-]+)[ \t]", data))
    _require(versions == {release.encode("ascii")}, "核心內嵌版本缺失、歧義或與指定版本不符")
    return {"format": "Image", "kernel_release": release, "image_size": size,
            "text_offset": offset, "flags": flags}


def _uncompress(data, remaining):
    try:
        if data.startswith(b"\x1f\x8b"):
            decoder = zlib.decompressobj(31)
            result = decoder.decompress(data, remaining + 1)
        elif data.startswith(b"\xfd7zXZ\0"):
            decoder = lzma.LZMADecompressor(format=lzma.FORMAT_XZ, memlimit=128 * 1024**2)
            result = decoder.decompress(data, max_length=remaining + 1)
        else:
            raise AmlogicError("initrd 壓縮格式未支援；不僅依 uInitrd 名稱或壓縮欄位判定")
    except (zlib.error, lzma.LZMAError) as exc:
        raise AmlogicError("initrd 壓縮資料毀損或記憶體需求超限") from exc
    _require(len(result) <= remaining and decoder.eof, "initrd 解壓超限或截斷")
    return result, decoder.unused_data


def _cpio(data, offset, releases):
    count = 0
    while True:
        _require(count < 200000 and offset + 110 <= len(data), "cpio 截斷或項目過多")
        header = data[offset:offset + 110]
        _require(header[:6] in (b"070701", b"070702") and
                 re.fullmatch(rb"[0-9a-fA-F]{104}", header[6:]) is not None, "cpio 標頭無效")
        fields = [int(header[index:index + 8], 16) for index in range(6, 110, 8)]
        size, namesize, check = fields[6], fields[11], fields[12]
        start = offset + 110
        _require(1 <= namesize <= 4096 and start + namesize <= len(data), "cpio 名稱長度無效")
        raw_name = data[start:start + namesize]
        _require(raw_name[-1:] == b"\0" and b"\0" not in raw_name[:-1], "cpio 名稱終止字元無效")
        try:
            name = raw_name[:-1].decode("utf-8")
        except UnicodeError as exc:
            raise AmlogicError("cpio 名稱不是 UTF-8") from exc
        _require(not name.startswith("/") and ".." not in name.split("/"), "cpio 含不安全路徑")
        start = (start + namesize + 3) & ~3
        end = start + size
        _require(end <= len(data), "cpio 內容截斷")
        if header[:6] == b"070702":
            _require(sum(data[start:end]) & 0xffffffff == check, "cpio 內容校驗不符")
        else:
            _require(check == 0, "newc 的校驗欄位非零")
        offset = (end + 3) & ~3
        _require(offset <= len(data), "cpio 對齊截斷")
        if name == "TRAILER!!!":
            _require(size == 0, "cpio 結尾含內容")
            return offset, count
        match = re.match(r"^(?:\./)?(?:usr/)?lib/modules/([^/]+)(?:/|$)", name)
        if match:
            releases.add(match[1])
        count += 1


def _initrd(data, release):
    payload, metadata = _legacy(data, kind=3)
    pending, used, entries, archives, releases = payload, 0, 0, 0, set()
    while pending:
        pending = pending.lstrip(b"\0")
        if not pending:
            break
        _require(archives < 128, "initrd 串接段過多")
        if pending.startswith((b"070701", b"070702")):
            end, count = _cpio(pending, 0, releases)
            used += end
            entries += count
            pending = pending[end:]
        else:
            expanded, tail = _uncompress(pending, MAX_UNPACKED - used)
            used += len(expanded)
            position = 0
            while position < len(expanded):
                while position < len(expanded) and expanded[position] == 0:
                    position += 1
                if position == len(expanded):
                    break
                position, count = _cpio(expanded, position, releases)
                entries += count
            pending = tail
        archives += 1
        _require(used <= MAX_UNPACKED and entries <= 200000, "initrd 展開大小或項目數超限")
    _require(entries > 0 and releases == {release}, "initrd 的 modules 版本缺失、混用或錯配")
    return {"format": "legacy", **metadata, "module_releases": sorted(releases), "entries": entries}


def _run(argv):
    try:
        result = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True, timeout=30,
                                check=False, env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"})
    except (OSError, subprocess.SubprocessError) as exc:
        raise ToolError("離線工具不存在、執行失敗或逾時：" + Path(argv[0]).name) from exc
    if result.returncode < 0:
        raise ToolError("離線工具遭訊號中斷：" + Path(argv[0]).name)
    _require(result.returncode == 0, "離線工具拒絕資料：" + Path(argv[0]).name)
    _require(len(result.stdout) <= 1024**2 and len(result.stderr) <= 1024**2, "離線工具輸出超限")
    return result.stdout


def _fdtget(path, node, prop=None, *, mode="x"):
    argv = ["/usr/bin/fdtget", "-" + mode] if mode in ("l", "p") else ["/usr/bin/fdtget", "-t", mode]
    try:
        return _run(argv + [str(path), node] + ([] if prop is None else [prop])).decode("ascii").strip().split()
    except UnicodeError as exc:
        raise AmlogicError("FDT 名稱或屬性含未支援字元") from exc


def _cells_number(cells):
    return sum(int(value, 16) << (32 * (len(cells) - index - 1)) for index, value in enumerate(cells))


def _cell_spans(cells, ac, sc):
    _require(cells and len(cells) % (ac + sc) == 0, "reserved-memory 區間長度不符")
    result = []
    for index in range(0, len(cells), ac + sc):
        address = _cells_number(cells[index:index + ac])
        size = _cells_number(cells[index + ac:index + ac + sc])
        _require(size > 0 and address + size <= 2**64, "reserved-memory 區間無效")
        result.append({"start": address, "size": size})
    return result


def _dynamic_cma(path, node, props, ac, sc):
    required = {"compatible", "reusable", "size", "alignment", "linux,cma-default"}
    allowed = required | {"alloc-ranges", "phandle", "linux,phandle", "status"}
    _require(required <= set(props) <= allowed and
             _fdtget(path, node, "compatible", mode="s") == ["shared-dma-pool"],
             "未知動態 reserved-memory；只支援明示對齊的預設 reusable CMA")
    _require(not _fdtget(path, node, mode="l"), "動態 CMA 不得包含未支援子節點")
    for prop in ("reusable", "linux,cma-default"):
        _require(not _fdtget(path, node, prop), "動態 CMA 布林屬性不得帶值")
    if "status" in props:
        _require(_fdtget(path, node, "status", mode="s") in (["okay"], ["ok"]), "動態 CMA 狀態未支援")
    size, alignment = _fdtget(path, node, "size"), _fdtget(path, node, "alignment")
    _require(len(size) == sc and len(alignment) == sc, "動態 CMA size／alignment cells 不符")
    size, alignment = _cells_number(size), _cells_number(alignment)
    _require(0 < size < 2**64 and 0 < alignment < 2**64 and alignment & (alignment - 1) == 0
             and size % alignment == 0, "動態 CMA 大小或對齊無效")
    ranges = _cell_spans(_fdtget(path, node, "alloc-ranges"), ac, sc) if "alloc-ranges" in props else []
    return {"node": node, "kind": "linux-cma-default", "size": size, "alignment": alignment,
            "alloc_ranges": ranges}


def _fdt(data, path, profile=None):
    _require(len(data) >= 64, "FDT 標頭截斷")
    magic, total, structure, strings, reserved, version, compatible, _, ssize, dsize = struct.unpack_from(">10I", data)
    _require(magic == 0xd00dfeed and total == len(data) and version == 17 and compatible <= 17,
             "FDT magic、版本或總長度不符")
    _require(40 <= structure <= total - dsize and structure % 4 == 0 and
             40 <= strings <= total - ssize and 40 <= reserved < total and reserved % 8 == 0,
             "FDT 區塊範圍或對齊無效")
    reservations, cursor = [], reserved
    while True:
        _require(cursor + 16 <= total, "FDT 保留表截斷")
        address, size = struct.unpack_from(">QQ", data, cursor)
        cursor += 16
        if (address, size) == (0, 0):
            break
        _require(size > 0 and address + size <= 2**64, "FDT 保留區無效")
        reservations.append({"start": address, "size": size})
    spans = [(0, 40), (structure, structure + dsize), (strings, strings + ssize), (reserved, cursor)]
    _require(all(max(a, c) >= min(b, d) for index, (a, b) in enumerate(spans)
                 for c, d in spans[:index]), "FDT 區塊重疊")
    _run(["/usr/bin/dtc", "-q", "-I", "dtb", "-O", "dtb", "-o", "/dev/null", str(path)])
    roots = _fdtget(path, "/", mode="l")
    _require("images" not in roots and "configurations" not in roots, "不接受 FIT 容器冒充 DTB")
    if profile is None:
        return {"format": "dtb"}
    actual = _fdtget(path, "/", "compatible", mode="s")
    _require(actual == profile["compatible"], "DTB 根 compatible 與板型／SoC 不符")
    dynamic_cma = []
    if "reserved-memory" in roots:
        _require(not _fdtget(path, "/reserved-memory", "ranges"), "reserved-memory 位址轉換未支援")
        ac = _fdtget(path, "/reserved-memory", "#address-cells")
        sc = _fdtget(path, "/reserved-memory", "#size-cells")
        _require(ac in (["1"], ["2"]) and sc in (["1"], ["2"]), "reserved-memory cells 未支援")
        ac, sc = int(ac[0]), int(sc[0])
        for node in _fdtget(path, "/reserved-memory", mode="l"):
            node = "/reserved-memory/" + node
            props = _fdtget(path, node, mode="p")
            if "reg" in props:
                _require("size" not in props, "reserved-memory 不得混用靜態 reg 與動態 size")
                reservations.extend(_cell_spans(_fdtget(path, node, "reg"), ac, sc))
            else:
                dynamic_cma.append(_dynamic_cma(path, node, props, ac, sc))
                _require(len(dynamic_cma) == 1, "不得指定多個預設動態 CMA")
    return {"format": "dtb", "compatible": actual, "reservations": reservations, "dynamic_cma": dynamic_cma}


def _environment(env, profile):
    unknown = sorted(set(env) - ENV_KEYS)
    _require(not unknown, "未實作的環境／fixup 參數：" + ", ".join(unknown))
    _require(env.get("fdtfile") == profile["dtb"], "fdtfile 未明示或與板型來源不符")
    _require(_match(r"(?:UUID=[0-9a-fA-F-]+|PARTUUID=[0-9a-fA-F-]+|/dev/[A-Za-z0-9_./-]+)",
                    env.get("rootdev")), "rootdev 必須明示；不沿用腳本的 MMC 預設")
    _require(env.get("rootfstype", "ext4") in ("ext4", "ext3", "ext2", "btrfs", "f2fs"), "rootfstype 未支援")
    _require(env.get("console", "both") in ("both", "serial", "display"), "console 未支援")
    _require(env.get("verbosity", "1") in tuple(str(n) for n in range(8)), "verbosity 無效")
    for key, default, allowed in (("bootlogo", "false", ("false", "true")),
                                   ("disable_vu7", "true", ("false", "true")),
                                   ("docker_optimizations", "on", ("on", "off"))):
        _require(env.get(key, default) in allowed, "環境值未支援：" + key)
    _require(_match(r"[A-Za-z0-9_,:.-]*", env.get("usbstoragequirks", "")), "usbstoragequirks 無效")
    for key in ("extraargs", "extraboardargs"):
        args = env.get(key, "").split()
        _require(all(_match(r"[A-Za-z0-9_./,:=+@%-]{1,256}", arg) and not arg.startswith("-") for arg in args),
                 "額外核心參數含未支援字元")
        _require(not any(arg.split("=", 1)[0] in ("root", "rootfstype", "ubootpart") for arg in args),
                 "額外核心參數不得覆寫根媒體身分")
    return env


def _bootargs(env, partuuid):
    args = ["root=" + env["rootdev"], "rootwait", "rootfstype=" + env.get("rootfstype", "ext4")]
    args += ["splash", "plymouth.ignore-serial-consoles"] if env.get("bootlogo", "false") == "true" else ["splash=verbose"]
    args += ["console=ttyAML0,115200"]
    if env.get("console", "both") != "serial":
        args += ["console=tty1"]
    args += ["consoleblank=0", "coherent_pool=2M", "loglevel=" + env.get("verbosity", "1"),
             "ubootpart=" + partuuid, "libata.force=noncq", "usb-storage.quirks=" + env.get("usbstoragequirks", "")]
    if env.get("disable_vu7", "true") == "false":
        args += ["usbhid.quirks=0x0eef:0x0005:0x0004"]
    args += env.get("extraargs", "").split() + env.get("extraboardargs", "").split()
    if env.get("docker_optimizations", "on") == "on":
        args += ["cgroup_enable=memory"]
    return args


def _prepare(capture, board, release):
    manifest, profile = capture.manifest, PROFILES[board]
    source_paths = (profile["source"], "config/sources/families/" + profile["family"] + ".conf",
                    "config/sources/families/include/meson64_common.inc", "config/bootenv/meson.txt", SCRIPT, NOOP_FIXUP)
    for path in source_paths:
        def source(path=path):
            data = (ROOT / path).read_bytes()
            record = capture.save("sources/" + path, data, "source")
            manifest["sources"].append({"source_path": path, "sha256": record["sha256"]})
            _require(_sha(data) == SOURCE_HASHES[path], "建置來源已變更，須重新核定解析語意：" + path)
        capture.attempt("sources", source)

    release_data = capture.read("/etc/armbian-release", required=True, maximum=65536)
    env_data = capture.read("/boot/armbianEnv.txt", required=True, maximum=65536)
    rel = capture.attempt("release", lambda: _assignments(release_data, capture, shell=True)) or {}
    env = capture.attempt("environment", lambda: _assignments(env_data, capture)) or {}
    manifest["release"], manifest["environment"] = rel, env
    rootdev = env.get("rootdev", "")
    if _match(r"UUID=[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", rootdev):
        manifest["root_uuid"] = rootdev[5:].lower()
    for key, expected in (("BOARD", board), ("BOARDFAMILY", profile["family"]), ("LINUXFAMILY", "meson64"),
                          ("ARCH", "arm64")):
        if rel.get(key) != expected:
            capture.block("release", "release 與指定板型不符：" + key)
    for key in ("KERNEL_VERSION", "KERNEL_RELEASE"):
        if key in rel and rel[key] != release:
            capture.block("release", "release 核心版本與指定版本不符：" + key)
    capture.attempt("environment", lambda: _environment(env, profile))

    cmd = capture.read("/boot/boot.cmd", required=True, maximum=1024**2)
    scr = capture.read("/boot/boot.scr", required=True, maximum=1024**2)
    if cmd is not None:
        if _sha(cmd) != SOURCE_HASHES[SCRIPT]:
            capture.block("boot.cmd", "boot.cmd 不符合已核定 boot-meson64 來源，不能推測執行語意")
    if scr is not None:
        decoded = capture.attempt("boot.scr", lambda: _legacy(scr, kind=6))
        if decoded is not None:
            script, metadata = decoded
            capture.save("derived/boot-script.cmd", script, "decoded-script")
            manifest["boot_script"] = metadata
            if script != cmd or _sha(script) != SOURCE_HASHES[SCRIPT]:
                capture.block("boot.scr", "生效 boot.scr 與 boot.cmd／核定來源不一致")
    for path in ALTERNATE_ENTRIES:
        if capture.read(path, maximum=1024**2) is not None:
            capture.block(path, "存在替代或自訂引導入口，尚未核定其優先序／語意")

    legacy = capture.read("/boot/zImage")
    manifest["branch"] = "legacy" if legacy is not None else "modern"
    if legacy is not None:
        capture.block("legacy", "zImage 使原腳本走 legacy unzip 分支，尚未適配；不改用現代 Image")
    kernel = capture.read("/boot/Image", required=legacy is None)
    initrd = capture.read("/boot/uInitrd", required=True)
    dtb_path = "/boot/dtb/" + profile["dtb"]
    if env.get("fdtfile") and env["fdtfile"] != profile["dtb"]:
        capture.attempt("fdtfile", lambda: capture.read("/boot/dtb/" + env["fdtfile"], maximum=8 * 1024**2))
    dtb = capture.read(dtb_path, required=True, maximum=8 * 1024**2)
    for name, data, path, check in (
            ("kernel", kernel, "/boot/Image", lambda: _kernel(kernel, release)),
            ("initrd", initrd, "/boot/uInitrd", lambda: _initrd(initrd, release)),
            ("dtb", dtb, dtb_path, lambda: _fdt(dtb, capture.output / ("files" + dtb_path), profile))):
        if data is not None:
            result = capture.attempt(name, check)
            if result is not None:
                manifest["components"][name] = {"path": "files" + path, "bytes": len(data), "sha256": _sha(data), **result}

    overlays = []
    prefix = env.get("overlay_prefix", "")
    safe_prefix = _match(r"[A-Za-z0-9_-]{0,80}", prefix)
    if not safe_prefix:
        capture.block("overlays", "overlay_prefix 不安全")
    for key, directory in (("overlays", "/boot/dtb/amlogic/overlay/"), ("user_overlays", "/boot/overlay-user/")):
        names = env.get(key, "").split()
        if len(names) > 64 or len(set(names)) != len(names):
            capture.block(key, "overlay 過多或重複")
            continue
        for name in names:
            if not _match(r"[A-Za-z0-9_-]{1,80}", name) or (key == "overlays" and not safe_prefix):
                capture.block(key, "overlay 名稱或前綴不安全")
                continue
            path = directory + (prefix + "-" if key == "overlays" else "") + name + ".dtbo"
            data = capture.read(path, required=True, maximum=8 * 1024**2)
            manifest["overlays"].append(path)
            if data is not None:
                local = capture.output / ("files" + path)
                if capture.attempt(path, lambda data=data, local=local: _fdt(data, local)) is not None:
                    overlays.append(local)
    fixup_paths = ["/boot/fixup.scr"]
    if safe_prefix:
        fixup_paths.insert(0, "/boot/dtb/amlogic/overlay/" + prefix + "-fixup.scr")
    for path in fixup_paths:
        data = capture.read(path, maximum=1024**2)
        if data is not None:
            fixup = {"image_path": path, "status": "blocked"}
            manifest["fixups"].append(fixup)
            decoded = capture.attempt(path, lambda data=data: _legacy(data, kind=6))
            if decoded is not None:
                capture.save("decoded" + path + ".cmd", decoded[0], "decoded-fixup")
                if path != "/boot/fixup.scr" and _sha(decoded[0]) == SOURCE_HASHES[NOOP_FIXUP]:
                    fixup.update(status="verified-noop", script_sha256=_sha(decoded[0]))
                    continue
            capture.block(path, "fixup 尚未有等價離線實作；不可略過且絕不執行映像腳本")
    # 缺檔、未知參數或 fixup 都可能改變結果；不能只合併已知的部分再宣稱可引導。
    if overlays and not manifest["blockers"]:
        def apply_overlays():
            local = capture.output / "derived/board.dtb"
            local.parent.mkdir(parents=True, exist_ok=True)
            _run(["/usr/bin/fdtoverlay", "-i", str(capture.output / ("files" + dtb_path)),
                  "-o", str(local), *map(str, overlays)])
            data = local.read_bytes()
            record = {"path": "derived/board.dtb", "bytes": len(data), "sha256": _sha(data), "role": "merged-dtb"}
            manifest["files"].append(record)
            result = _fdt(data, local, profile)
            manifest["components"]["dtb"] = {**record, **result}
            manifest["overlay_applied"] = True
        capture.attempt("fdtoverlay", apply_overlays)
    if set(manifest["components"]) != {"kernel", "initrd", "dtb"}:
        capture.block("components", "未取得完整且核對通過的原配組件")
    manifest["bootargs_pattern"] = _bootargs(env, "{partuuid}") if "rootdev" in env else []


def prepare(read_file, *, board, kernel_release, output):
    """回傳並保存 manifest；資料阻擋不拋例外，API／輸出錯誤才拋例外。

    read_file 接受映像內絕對路徑並回傳 bytes，只有 FileNotFoundError 表示不存在。
    symlink、映像摘要與分割區根目錄由呼叫端的唯讀提供者負責。
    """
    _require(callable(read_file), "read_file 必須可呼叫")
    _require(isinstance(board, str) and board in PROFILES, "只支援 CM4IO、M2Pro、M2S、M5 的精確板名")
    _require(_match(r"[A-Za-z0-9_.+~-]{1,128}", kernel_release), "核心版本必須明示且格式安全")
    output = Path(output).absolute()
    _require(".." not in output.parts, "輸出路徑不得含上層跳轉")
    output.mkdir(mode=0o700, exist_ok=False)
    capture = _Capture(read_file, output, board, kernel_release)
    try:
        _prepare(capture, board, kernel_release)
    except Exception:
        capture.block("interrupted", "準備未完成；保留既有證據，不得引導")
        capture.finish()
        raise
    return capture.finish()


def _read_evidence(path, maximum):
    """以同一描述符取得全部內容；拒絕路徑任一層的 symlink 與特殊檔。"""
    path = Path(path).absolute()
    _require(".." not in path.parts, "證據路徑不得含上層跳轉")
    descriptor = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        for part in path.parts[1:-1]:
            following = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                                dir_fd=descriptor)
            os.close(descriptor)
            descriptor = following
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=descriptor)
        with os.fdopen(fd, "rb") as stream:
            before = os.fstat(stream.fileno())
            _require(stat.S_ISREG(before.st_mode) and before.st_size <= maximum, "證據不是有界一般檔案")
            data = stream.read(maximum + 1)
            after = os.fstat(stream.fileno())
            _require(len(data) == before.st_size and len(data) <= maximum and
                     (before.st_size, before.st_mtime_ns, before.st_ctime_ns) ==
                     (after.st_size, after.st_mtime_ns, after.st_ctime_ns), "證據於讀取期間變更")
            return data
    finally:
        os.close(descriptor)


def _validate_cma(config, requirements, approval):
    if not requirements:
        _require(approval is None, "DTB 沒有動態 CMA，不接受無對應需求的核定")
        return
    _require(type(approval) is dict and set(approval) == {"requirements", "qualification_sha256"}
             and approval["requirements"] == requirements
             and _match(r"[0-9a-f]{64}", approval["qualification_sha256"]),
             "動態 CMA 必須由外部逐項明確核定需求及證據 SHA-256")
    _require(not any(arg.split("=", 1)[0] in ("cma", "numa_cma", "cma_pernuma", "mem", "memmap")
                     for arg in config["bootargs"]), "核心參數會改變已核定 CMA／RAM 語意，尚未支援")
    # 只檢查存在可容納需求的核定空間，不替 Linux 分配 CMA 位址，也不改寫 DTB。
    occupied = config["ram"]["reserved"] + [config["ram"]["kernel_work"]] + [
        {"start": item["address"], "size": item["capacity"]} for item in config["files"].values()]
    for requirement in requirements:
        free = []
        for bank in config["ram"]["banks"]:
            for region in requirement["alloc_ranges"] or config["ram"]["banks"]:
                start = max(bank["start"], region["start"])
                end = min(bank["start"] + bank["size"], region["start"] + region["size"])
                if start < end:
                    free.append((start, end))
        for reserved in occupied:
            start, end = reserved["start"], reserved["start"] + reserved["size"]
            next_free = []
            for left, right in free:
                if max(left, start) >= min(right, end):
                    next_free.append((left, right))
                else:
                    if left < start:
                        next_free.append((left, start))
                    if end < right:
                        next_free.append((end, right))
            free = next_free
        alignment = requirement["alignment"]
        _require(any((left + alignment - 1) // alignment * alignment + requirement["size"] <= right
                     for left, right in free), "核定 RAM 扣除靜態保留區與組件後不足以容納 CMA 需求")


def validate_boot_config(output, *, template):
    """重播已擷取證據，接上核定 template，呼叫通用離線驗證；不開啟 console。"""
    output = Path(output).absolute()
    raw = _read_evidence(output / "manifest.json", 1024**2)
    manifest = json.loads(raw.decode("utf-8"))
    _require(manifest.get("schema") == SCHEMA and manifest.get("status") == "prepared"
             and manifest.get("hardware_validated") is False, "manifest 尚未準備完成或混入硬體宣告")
    records = {}
    for record in manifest["reads"]:
        path = _image_path(record["image_path"])
        _require(path not in records and record["status"] in ("captured", "absent"), "讀取證據重複或不完整")
        records[path] = record

    def replay(path):
        _require(path in records, "缺少必要的存在性證據：" + path)
        record = records[path]
        if record["status"] == "absent":
            raise FileNotFoundError(path)
        local = output / ("files" + path)
        data = _read_evidence(local, MAX_BLOB)
        summary = {"bytes": len(data), "sha256": _sha(data)}
        _require(summary == {key: record[key] for key in ("bytes", "sha256")}, "擷取證據已變更：" + path)
        return data

    with tempfile.TemporaryDirectory(prefix="bpi-amlogic-verify-") as temp:
        checked = prepare(replay, board=manifest["board"], kernel_release=manifest["kernel_release"],
                          output=Path(temp) / "replay")
        _require(checked["status"] == "prepared", "證據重播遭阻擋，不接受手改 manifest 狀態")
        config = copy.deepcopy(template)
        _require(type(config) is dict and config.get("arch") == "arm64"
                 and config.get("kernel_release") == checked["kernel_release"], "template 架構或版本錯配")
        cma_approval = config.pop("amlogic_cma", None)
        parts = [arg.removeprefix("ubootpart=") for arg in config.get("bootargs", [])
                 if isinstance(arg, str) and arg.startswith("ubootpart=")]
        _require(len(parts) == 1 and _match(r"(?:[0-9a-f]{8}-[0-9a-f]{2}|[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})", parts[0]),
                 "template 必須提供唯一且核定的 ubootpart PARTUUID")
        _require(config["bootargs"] == _bootargs(checked["environment"], parts[0]),
                 "template bootargs 不等價於原配腳本／環境；不得遺漏或覆寫")
        if config.get("source", {}).get("type") == "mmc":
            _require(config["source"].get("partuuid") == parts[0], "MMC 載入媒體與 ubootpart 不符")
        _require(type(config.get("files")) is dict and set(config["files"]) == {"kernel", "initrd", "dtb"},
                 "template 必須明示三個組件的 RAM 配置")
        for name, component in checked["components"].items():
            _require(type(config["files"][name]) is dict, "template 組件配置缺失")
            config["files"][name].update({key: component[key] for key in ("path", "bytes", "sha256", "format")})
        config = uboot.validate_config(config)
        for span in checked["components"]["dtb"]["reservations"]:
            _require(any(item["start"] <= span["start"] and span["start"] + span["size"] <= item["start"] + item["size"]
                         for item in config["ram"]["reserved"]), "DTB 保留區未包含於外部核定 RAM 保留區")
        _validate_cma(config, checked["components"]["dtb"]["dynamic_cma"], cma_approval)
        uboot.validate_artifacts(config, output)
    return {"config": config, "artifacts_verified": True, "hardware_validated": False,
            "boot_config_validated": True, "executed": False, "amlogic_cma": cma_approval}
