#!/usr/bin/env python3
"""Allwinner ARM32／ARM64 原配組件準備；不執行映像腳本、不操作硬體。"""

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

try:
    from . import bpi_h618_artifacts as safe
    from . import bpi_lab_cma as cma
    from . import bpi_lab_uboot as uboot
except ImportError:
    import bpi_h618_artifacts as safe
    import bpi_lab_cma as cma
    import bpi_lab_uboot as uboot


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "bpi-lab-allwinner-components-v1"
MAX_FILE = 128 * 1024**2
MAX_EXPANDED = 256 * 1024**2
MAX_TEXT = 256 * 1024
POLICIES = {
    "bpi-6204": "r40-6204-legacy", "bpi-m1": "a20-current",
    "bpi-m1p": "a20-m1plus-current", "bpi-pro": "a20-current",
    "bpi-m2": "a31s-current", "bpi-m2b": "r40-current",
    "bpi-m2u": "r40-current", "bpi-m2m": "a33-current",
    "bpi-m2p": "h3-current", "bpi-m2z": "h2plus-current",
    "bpi-p2z": "h2plus-current", "bpi-m3": "a83t-current", "bpi-m64": "a64-current",
    "bpi-m4b": "h618-m4berry", "bpi-m4z": "h618-m4zero", "bpi-m4z-emac": "h618-m4zero-emac",
}
# H618 沒有獨立驗證 JSON；以已審閱且摘要固定的本倉 DTS 綁定根節點身分。
H618_PROFILES = {
    "bpi-m4b": {"dtb": "sun50i-h618-bananapi-m4-berry.dtb", "model": "BananaPi M4 Berry",
                 "source_structure_sha256": "962df0097208fd4b1324f7f3e54043a6272da438b666bd08bb95a2d731d361e0",
                 "compatible": ["BiPai,bananapi-m4berry", "allwinner,sun50i-h616"],
                 "sha256": "f13f2f4dde98c6a710db30485f8f766e37759755afffee243c50406a2c093a10"},
    "bpi-m4z": {"dtb": "sun50i-h618-bananapi-m4-zero.dtb", "model": "BananaPi BPI-M4-Zero",
                 "source_structure_sha256": "c62aa1b70240312c7a9aed20394f7602f6016a27df075cc75dd8909dae4666f1",
                 "compatible": ["sinovoip,bpi-m4-zero", "allwinner,sun50i-h618"],
                 "sha256": "53970883320c770cef902da3d2bbbcc1c420ff911e4b7f675ee162a6633ecfc1"},
    "bpi-m4z-emac": {"dtb": "sun50i-h618-bananapi-m4-zero-emac.dtb", "model": "BananaPi BPI-M4-Zero EMAC",
                      "source_structure_sha256": "8960e0bf172e54dc8681cfaad829a2934d94a5a4d7ca217f533d93e3a8ba751c",
                      "compatible": ["sinovoip,bpi-m4-zero-emac", "sinovoip,bpi-m4-zero", "allwinner,sun50i-h618"],
                      "sha256": "e2c1f74af1560feab34188bef54113d08f44513615e3ba622c9dac836f6a1e68"},
}
H618_FAMILY_STRUCTURE_SHA256 = "3f505c34149fb4352bdbccf3968adbffe575eb266b8532a4bf4adff8ec7a50b6"
M4ZERO_MODEL_OVERLAY = {
    "path": "/boot/dtb/allwinner/overlay/sun50i-h616-bananapi-m4-sdio-wifi-bt.dtbo",
    "sha256": "6fba3c974e814542628b815350bf5c2be94664e082f9cf27d06bd5f357b96086",
    "source": "patch/kernel/archive/sunxi-6.18/overlay_64/sun50i-h616-bananapi-m4-sdio-wifi-bt.dtso",
    "source_sha256": "d7140eab49d76675d98a1f20226aced08b379d82a203e29936378e30e194bfb5",
    "model": "BananaPi BPI-M4-Zero v2",
}
# 只有已逐項審閱的 fixup 可在無參數時視為 DTB 無操作；會改寫 console 的 PWM 分支另行阻擋。
FIXUPS = {
    "sun7i-a20": ("overlay_32", "2f59c731905cef8af8ff5299335a81cdf361e698a1d177b05ffbdb60e520cf13"),
    "sun8i-h3": ("overlay_32", "dfd30fed731644cb902da750629b67a5a1b03fb23355eccd16f07710f910a161"),
    "sun50i-a64": ("overlay_64", "6a501ffce8fea1ec4b0581d58ccdbe17b6b3e05e9bfbfd744a419d5f2149d9a0"),
    "sun50i-h616": ("overlay_64", "31bfcd6f3d63c39b3587b8456ce02943b8aa18f0ef8f68ecbdcce6bf82afbdae"),
}
ENV_KEYS = set("fdtfile fdtdir overlay_prefix overlays user_overlays rootdev rootfstype verbosity "
               "console bootlogo docker_optimizations disp_mem_reserves disp_mode earlycon "
               "usbstoragequirks extraargs extraboardargs".split())
TOKEN = r"[A-Za-z0-9_./,:=+@%-]{1,256}"
NAME = r"[A-Za-z0-9][A-Za-z0-9_.+-]{0,95}"


class AllwinnerError(ValueError):
    """準備或組件綁定條件不符合。"""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def require(condition, code, message):
    if not condition:
        raise AllwinnerError(code, message)


def digest(blob):
    return {"bytes": len(blob), "sha256": hashlib.sha256(blob).hexdigest()}


def _json(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()


def _path(value):
    require(isinstance(value, str) and value.startswith("/boot/")
            and re.fullmatch(r"/[A-Za-z0-9_.+-]+(?:/[A-Za-z0-9_.+-]+)*", value)
            and not any(p in (".", "..") for p in value.split("/")),
            "image_path", "映像開機路徑無效或包含跳轉")
    return value


def _env(blob, *, release=False):
    require(len(blob) <= MAX_TEXT, "env_size", "環境文字超過上限")
    result = {}
    for line in blob.decode("utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, sep, value = line.partition("=")
        require(sep and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key) and key not in result,
                "env_syntax", "環境欄位無效或重複")
        if release:
            # release 是 shell 單純賦值，分號在引號內只是文字；不接受任何展開或引號拼接。
            require(re.fullmatch(r'''(?:'[^'\x00-\x1f$`\\]*'|"[^"\x00-\x1f$`\\]*"|[A-Za-z0-9_./,:=+@%~?!#-]*)''', value),
                    "env_syntax", "release 值不是單純文字賦值，或包含展開／控制字元")
            value = shlex.split(value, comments=False, posix=True)[0] if value else ""
        else:
            require(not any(c in value for c in "\x00\r\t$`\\\"';|&<>")
                    and all(ord(c) >= 32 for c in value), "env_syntax", "環境值含未支援的引號、展開或控制字元")
        result[key] = value
    return result


def _legacy(blob, *, script=False, arch="arm32"):
    require(len(blob) >= 64, "legacy_header", "U-Boot 組件標頭截斷")
    magic, crc, _, size, _, _, data_crc, system, cpu, kind, compression, _ = struct.unpack(">7I4B32s", blob[:64])
    payload = blob[64:]
    require(magic == 0x27051956 and size == len(payload), "legacy_header", "U-Boot 標頭或長度不符")
    require(zlib.crc32(blob[:4] + bytes(4) + blob[8:64]) == crc
            and zlib.crc32(payload) == data_crc, "legacy_crc", "U-Boot 標頭或資料 CRC 不符")
    require(system == 5 and cpu == {"arm32": 2, "arm64": 22}[arch]
            and kind == (6 if script else 3), "legacy_type", "U-Boot 組件用途或架構不符")
    if script:
        require(compression == 0 and len(payload) >= 8, "script_format", "腳本封裝不是未壓縮單項格式")
        size, end = struct.unpack_from(">II", payload)
        require(end == 0 and size == len(payload) - 8, "script_format", "腳本資料表不符")
        return payload[8:]
    # 建置來源固定 -C gzip；實際資料仍另外辨識，絕不只相信此欄位。
    require(compression == 1, "initrd_format", "uInitrd 壓縮標記不符合本倉封裝方式")
    return payload


def _decompress(blob):
    if blob.startswith(b"\x1f\x8b\x08"):
        decoder = zlib.decompressobj(31)
        data = decoder.decompress(blob, MAX_EXPANDED + 1)
    elif blob.startswith(b"\xfd7zXZ\x00"):
        decoder = lzma.LZMADecompressor(format=lzma.FORMAT_XZ, memlimit=MAX_EXPANDED)
        data = decoder.decompress(blob, max_length=MAX_EXPANDED + 1)
    else:
        raise AllwinnerError("compression", "只實作 gzip／XZ 解壓，其他壓縮格式保持阻擋")
    require(len(data) <= MAX_EXPANDED and decoder.eof, "decompression", "解壓超界、截斷或壓縮內容不完整")
    return data, decoder.unused_data


def _kernel(blob, arch, release, kernel_config=None):
    """未提供 config 的共用呼叫只核對格式／版本，不宣告核心配置已驗證。"""
    require(len(blob) >= 64, "kernel_format", "核心標頭截斷")
    if arch == "arm64":
        offset, size, flags = struct.unpack_from("<3Q", blob, 8)
        require(blob[56:60] == b"ARM\x64" and len(blob) <= size <= MAX_EXPANDED and not flags & 1,
                "kernel_format", "不是支援的 ARM64 raw Image")
        expanded = blob
        detail = {"format": "Image", "image_size": size, "text_offset": offset}
    else:
        magic, start, end = struct.unpack_from("<3I", blob, 36)
        require(magic == 0x016f2818 and end > start and end - start == len(blob),
                "kernel_format", "不是完整 ARM32 zImage")
        candidates = []
        for signature in (b"\x1f\x8b\x08", b"\xfd7zXZ\x00"):
            candidates.extend(match.start() for match in re.finditer(re.escape(signature), blob))
        require(0 < len(candidates) <= 16, "kernel_compression", "zImage 壓縮入口缺失、過多或未支援")
        found = []
        for index in sorted(candidates):
            try:
                data, _ = _decompress(blob[index:])
            except (AllwinnerError, zlib.error, lzma.LZMAError):
                continue
            if re.search(rb"Linux version ([^\s\x00]+)", data):
                found.append((index, data))
        require(len(found) == 1, "kernel_version", "zImage 無唯一可驗證的壓縮核心版本")
        index, expanded = found[0]
        detail = {"format": "zImage", "compressed_offset": index, "expanded_bytes": len(expanded)}
    versions = set(re.findall(rb"Linux version ([^\s\x00]+)", expanded))
    require(versions == {release.encode()}, "kernel_version", "核心內嵌版本與指定版本不符或不唯一")
    detail["kernel_config_verified"] = False
    markers = [match.end() for match in re.finditer(b"IKCFG_ST", expanded)] if kernel_config is not None else []
    if markers:
        require(len(markers) == 1, "kernel_config", "核心內嵌配置入口不唯一")
        embedded, tail = _decompress(expanded[markers[0]:])
        require(tail.startswith(b"IKCFG_ED") and embedded == kernel_config,
                "kernel_config", "原配 config 缺失或與核心內嵌配置不同")
        detail["embedded_config"] = digest(embedded)
        detail["kernel_config_verified"] = True
    return {**detail, "kernel_release": release}


def _initramfs(blob, release):
    """只解析有界 newc 結構；不解出檔案，也不執行其中的 init。"""
    pending, total, entries, versions, init = blob, 0, 0, set(), False
    while pending.strip(b"\x00"):
        pending = pending.lstrip(b"\x00")
        if not pending.startswith((b"070701", b"070702")):
            expanded, tail = _decompress(pending)
            total += len(expanded)
            require(total <= MAX_EXPANDED, "initrd_size", "initramfs 累計展開超界")
            pending = expanded + tail
            continue
        position = 0
        while True:
            header = pending[position:position + 110]
            require(len(header) == 110 and header[:6] in (b"070701", b"070702")
                    and re.fullmatch(rb"[0-9a-fA-F]{104}", header[6:]), "cpio", "newc 標頭截斷或格式不符")
            values = [int(header[i:i + 8], 16) for i in range(6, 110, 8)]
            size, namesize, checksum = values[6], values[11], values[12]
            require(1 <= namesize <= 4096, "cpio", "newc 名稱長度無效")
            begin = position + 110
            name = pending[begin:begin + namesize]
            require(len(name) == namesize and name.endswith(b"\x00") and b"\x00" not in name[:-1],
                    "cpio", "newc 名稱截斷或含內嵌終止字元")
            name = name[:-1].decode("utf-8")
            start = (begin + namesize + 3) & ~3
            end = start + size
            require(end <= len(pending), "cpio", "newc 資料截斷")
            if header[:6] == b"070702":
                require(sum(pending[start:end]) & 0xffffffff == checksum, "cpio_crc", "newc CRC 不符")
            position = (end + 3) & ~3
            if name == "TRAILER!!!":
                require(size == 0, "cpio", "newc 結尾長度不符")
                break
            require(not name.startswith("/") and ".." not in name.split("/"), "cpio_path", "initramfs 含不安全路徑")
            entries += 1
            require(entries <= 200000, "cpio_size", "initramfs 項目數超界")
            init |= name.removeprefix("./") == "init"
            match = re.match(r"(?:\./)?(?:usr/)?lib/modules/([^/]+)(?:/|$)", name)
            if match:
                versions.add(match[1])
        pending = pending[position:]
    require(init and versions == {release}, "initrd_version", "initramfs 缺少 init 或模組版本不唯一／不符")
    return {"module_versions": sorted(versions), "entries": entries, "expanded_bytes": total}


class _Evidence:
    def __init__(self, output, read_file, manifest):
        self.output = Path(output).absolute()
        self.read_file, self.manifest, self.cache = read_file, manifest, {}
        self.read_status = {}
        require(".." not in self.output.parts, "output", "證據目錄不得含上層跳轉")
        fd = os.open(self.output.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        try:
            for part in self.output.parts[1:-1]:
                next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
                os.close(fd)
                fd = next_fd
            os.mkdir(self.output.name, 0o700, dir_fd=fd)
            self.fd = os.open(self.output.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
        finally:
            os.close(fd)
        os.mkdir("files", 0o700, dir_fd=self.fd)
        os.mkdir("analysis", 0o700, dir_fd=self.fd)

    def save(self, name, blob):
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=self.fd)
        with os.fdopen(fd, "wb") as stream:
            stream.write(blob)
            stream.flush()
            os.fsync(stream.fileno())

    def block(self, code, message):
        record = {"code": code, "message": message}
        if record not in self.manifest["blockers"]:
            self.manifest["blockers"].append(record)

    def check(self, label, function):
        try:
            value = function()
            self.manifest["checks"][label] = value if value is not None else True
            return value
        except (AllwinnerError, cma.CMAError) as exc:
            self.block(exc.code, f"{label}：{exc}")
        except (ValueError, struct.error, zlib.error, lzma.LZMAError):
            self.block("invalid_data", f"{label}：資料格式無效或解析失敗")
        except OSError:
            self.block("io_error", f"{label}：來源或解析工具無法讀取")
        return None

    def source(self, path):
        blob = (ROOT / path).read_bytes()
        require(len(blob) <= 2 * 1024**2, "source_size", "來源設定超過大小上限")
        self.manifest["sources"][path] = digest(blob)
        return blob

    def get(self, path, role, *, required=False, maximum=MAX_FILE):
        if path not in self.cache:
            try:
                blob = self.read_file(path)
            except FileNotFoundError:
                self.manifest["reads"].append({"path": path, "status": "missing"})
                self.read_status[path] = "missing"
                self.cache[path] = None
            except Exception:
                self.manifest["reads"].append({"path": path, "status": "failed"})
                self.read_status[path] = "failed"
                self.block("reader_failed", f"唯讀介面讀取失敗：{path}")
                self.cache[path] = None
            else:
                require(type(blob) is bytes, "reader_contract", "唯讀介面必須回傳 bytes")
                require(len(blob) <= maximum, "file_size", f"組件超過大小上限：{path}")
                name = f"files/{len(self.cache):04d}.bin"
                self.save(name, blob)
                self.cache[path] = (blob, {"path": path, "evidence_path": name, **digest(blob)})
                self.manifest["reads"].append({"path": path, "status": "saved"})
        item = self.cache[path]
        if item is None:
            if required and self.read_status[path] == "missing":
                self.block("missing_file", f"缺少原配檔案：{path}")
            return None
        blob, record = item
        self.manifest["files"][role] = record
        return blob

    def run(self, argv):
        index = len(self.manifest["commands"])
        record = {"argv": [str(a) for a in argv], "returncode": None}
        self.manifest["commands"].append(record)
        try:
            result = subprocess.run(record["argv"], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, timeout=30, check=False,
                                    env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"})
            stdout, stderr = result.stdout, result.stderr
            record["returncode"] = result.returncode
        except subprocess.TimeoutExpired as exc:
            stdout, stderr = exc.stdout or b"", exc.stderr or b""
            record["timeout"] = True
        except OSError:
            stdout, stderr = b"", b""
            record["unavailable"] = True
        for suffix, data in (("stdout", stdout), ("stderr", stderr)):
            name = f"analysis/{index:03d}.{suffix}"
            self.save(name, data)
            record[suffix] = {"evidence_path": name, **digest(data)}
        require(record["returncode"] == 0, "host_tool", f"本機解析工具失敗或逾時：{argv[0]}")
        return stdout


def _source_literals(blob, fields):
    """只辨識單行常值賦值；另回傳結構摘要，讓呼叫端核定生效範圍。"""
    values, structure = {}, []
    names = "|".join(re.escape(field) for field in fields)
    literal = r'''(?:'[A-Za-z0-9_./,:+\-]+'|"[A-Za-z0-9_./,:+\-]+"|[A-Za-z0-9_./,:+\-]+)'''
    for line in blob.decode().splitlines(keepends=True):
        if line.lstrip().startswith("#") or not re.search(r"\b(?:" + names + r")\b", line):
            structure.append(line)
            continue
        match = re.fullmatch(r"[ \t]*(?:declare[ \t]+-g[ \t]+)?(" + names + r")=(" + literal
                             + r")(?:[ \t]+#[^\n]*)?[ \t]*\n?", line)
        require(match is not None, "source_mapping", "來源設定不是已實作的單行常值，須重新審閱")
        key, value = match.groups()
        require(key not in values, "source_mapping", "來源設定重複或可能遭覆寫：" + key)
        values[key] = shlex.split(value, comments=False, posix=True)[0]
        structure.append(key + "=<literal>\n")
    return values, digest("".join(structure).encode())["sha256"]


def _profile(evidence, board):
    registry = json.loads(evidence.source("docs/evidence/bpi-multiboard-lab-20260917/board-registry.json"))
    rows = [row for row in registry["boards"] if row["board"] in POLICIES]
    matches = [row for row in rows if board in (row["board"], row["artifact_board"])]
    require(len(rows) == len(POLICIES) and len(matches) == 1, "board", "板型不在已核對的 16 板範圍")
    row = matches[0]
    if row["board"] in H618_PROFILES:
        policy = {**H618_PROFILES[row["board"]], "family": "sun50iw9-bpi", "overlay_prefix": "sun50i-h616"}
        source = "patch/kernel/archive/sunxi-6.18/dt_64/" + policy["dtb"].removesuffix(".dtb") + ".dts"
        require(digest(evidence.source(source))["sha256"] == policy["sha256"],
                "source_mapping", "H618 DTS 身分來源已改動，須重新審閱")
        family = evidence.source("config/sources/families/sun50iw9-bpi.conf")
        settings, structure = _source_literals(family, ("BOOTSCRIPT",))
        # 固定其餘 shell 結構，證明設定仍位於已審閱的 current／edge 分支；不執行 shell。
        require(settings == {"BOOTSCRIPT": "boot-sun50i-next.cmd:boot.cmd"}
                and structure == H618_FAMILY_STRUCTURE_SHA256
                and row["architecture"] == "arm64", "source_mapping", "H618 腳本來源或架構錯配")
    else:
        policy_path = f"config/validation/bananapi-sunxi-{POLICIES[row['board']]}.json"
        policy = json.loads(evidence.source(policy_path))["boards"][row["artifact_board"]]
    board_source = evidence.source(row["config_path"])
    settings, structure = _source_literals(board_source, ("BOARDFAMILY", "BOOT_FDT_FILE", "OVERLAY_PREFIX"))
    require(settings.get("BOARDFAMILY") == row["family"]
            and policy["family"] == row["family"] and row["architecture"] in ("arm32", "arm64"),
            "source_mapping", "板型、家族及驗證來源錯配")
    if row["board"] in H618_PROFILES:
        require(structure == policy["source_structure_sha256"]
                and set(settings) == {"BOARDFAMILY", "BOOT_FDT_FILE", "OVERLAY_PREFIX"},
                "source_mapping", "H618 來源有效設定缺漏或 shell 結構未知，須重新審閱")
        targets, _ = _source_literals(board_source, ("KERNEL_TARGET",))
        branch = re.fullmatch(r"[A-Za-z0-9_.+~]+-(current|edge)-sunxi64", evidence.manifest["kernel_release"])
        require(branch is not None and branch[1] in targets.get("KERNEL_TARGET", "").split(","),
                "source_mapping", "H618 核心分支不在已核對的有效 BOOTSCRIPT 與板型範圍")
    for field, expected in (("BOOT_FDT_FILE", policy["dtb"]), ("OVERLAY_PREFIX", policy["overlay_prefix"])):
        require(field not in settings or settings[field] == expected,
                "source_mapping", f"板型來源 {field} 與驗證設定不同")
    for source in row["config_sources"]:
        blob = evidence.source(source["path"])
        if row["board"] in H618_PROFILES and source["path"] not in (
                row["config_path"], "config/sources/families/sun50iw9-bpi.conf"):
            require(digest(blob)["sha256"] == source["sha256"],
                    "source_mapping", "H618 引用來源已改動，不能推定設定仍有效")
    script = "boot-sunxi.cmd" if row["architecture"] == "arm32" else "boot-sun50i-next.cmd"
    script_path = "config/bootscripts/" + script
    original = evidence.source(script_path)
    evidence.source("packages/bsp/common/etc/initramfs/post-update.d/99-uboot")
    return {"board": row["board"], "artifact_board": row["artifact_board"], "arch": row["architecture"],
            "family": row["family"], "dtb": policy["dtb"].removeprefix("allwinner/"),
            "model": policy["model"], "compatible": policy["compatible"],
            "overlay_prefix": policy["overlay_prefix"], "script": script_path}, original


def _check_env(env, profile):
    unknown = sorted(set(env) - ENV_KEYS)
    require(not unknown, "unsupported_env", "尚未實作的環境欄位：" + ", ".join(unknown))
    require(re.fullmatch(r"UUID=[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", env.get("rootdev", "")),
            "rootdev", "必須由原環境明示 rootdev=UUID，不猜 MMC 編號或根 UUID")
    require(env.get("overlay_prefix") == profile["overlay_prefix"], "overlay_prefix", "overlay_prefix 與板型來源錯配")
    require(env.get("fdtfile", profile["dtb"]) in (profile["dtb"], "allwinner/" + profile["dtb"]),
            "dtb_name", "fdtfile 與板型來源錯配")
    if "fdtdir" in env:
        _path(env["fdtdir"])
    allowed = {"console": {"serial", "display", "both"}, "bootlogo": {"true", "false"},
               "docker_optimizations": {"on", "off"}, "earlycon": {"on", "off"},
               "disp_mem_reserves": {"on", "off"}, "rootfstype": {"ext4", "ext3", "ext2", "btrfs", "f2fs", "xfs"}}
    for key, values in allowed.items():
        require(key not in env or env[key] in values, "env_value", f"環境欄位 {key} 值不支援")
    require(re.fullmatch(r"[0-7]", env.get("verbosity", "1")), "env_value", "verbosity 必須介於 0 至 7")
    for key in ("extraargs", "extraboardargs"):
        args = env.get(key, "").split()
        require(all(re.fullmatch(TOKEN, arg) and not arg.startswith("-") for arg in args),
                "bootargs", f"{key} 含未支援的核心參數字元")
        reserved = {"root", "rootfstype", "rootwait", "console", "ubootpart", "ubootsource"}
        require(not any(arg.split("=", 1)[0] in reserved for arg in args), "bootargs", f"{key} 不得覆寫根媒體或引導身分")
    for key in ("disp_mode", "usbstoragequirks"):
        require(not env.get(key) or re.fullmatch(TOKEN, env[key]), "env_value", f"環境欄位 {key} 格式不支援")
    if profile["arch"] == "arm64":
        require(env.get("earlycon", "off") == "off" and env.get("disp_mem_reserves", "off") == "off"
                and env.get("disp_mode", "1920x1080p60") == "1920x1080p60",
                "unused_env", "A64 腳本不處理這些非預設顯示／earlycon 設定")
    return True


def _overlay_names(env, key):
    names = env.get(key, "").split()
    require(len(names) <= 32 and len(names) == len(set(names))
            and all(re.fullmatch(NAME, name) and name not in (".", "..") for name in names),
            "overlay_name", f"{key} 含無效、重複或過多項目")
    return names


def _overlay_paths(env, dtb_path):
    directory = str(Path(dtb_path).parent)
    prefix = env.get("overlay_prefix", "")
    require(re.fullmatch(NAME, prefix), "overlay_prefix", "overlay_prefix 不是安全名稱")
    return [_path(f"{directory}/overlay/{prefix}-{name}.dtbo" if key == "overlays"
                  else f"/boot/overlay-user/{name}.dtbo")
            for key in ("overlays", "user_overlays") for name in _overlay_names(env, key)]


def _effective_profile(evidence, profile):
    """只接受已審閱的 M4 Zero 無線 overlay 所宣告的型號字串變更。"""
    if profile["board"] != "bpi-m4z":
        return profile
    reviewed = M4ZERO_MODEL_OVERLAY
    for role in evidence.manifest.get("overlay_order", []):
        record = evidence.manifest["files"].get(role, {})
        if record.get("path") == reviewed["path"] and record.get("sha256") == reviewed["sha256"]:
            source = evidence.source(reviewed["source"])
            require(digest(source)["sha256"] == reviewed["source_sha256"],
                    "overlay_source", "M4 Zero 型號變更 overlay 來源已改變，須重新審閱")
            return {**profile, "model": reviewed["model"]}
    return profile


def _dtb(evidence, role, profile):
    if role == "effective_dtb":
        profile = _effective_profile(evidence, profile)
    record = evidence.manifest["files"][role]
    path = evidence.output / record["evidence_path"]
    # libfdt 解析實際結構，不能以二進位搜尋 compatible 字串取代根節點核對。
    compatible = evidence.run(["/usr/bin/fdtget", "-t", "s", path, "/", "compatible"]).decode().strip().split()
    model = evidence.run(["/usr/bin/fdtget", "-t", "s", path, "/", "model"]).decode().strip()
    require(compatible == profile["compatible"] and model == profile["model"],
            "dtb_identity", "DTB 根節點板型或 SoC 相容字串不符")
    data = path.read_bytes()
    require(len(data) >= 64, "dtb_format", "DTB 長度不足")
    magic, total, structure, strings, reservations, version, compat, _, strsize, stsize = struct.unpack_from(">10I", data)
    require(magic == 0xd00dfeed and total == len(data) and version == 17 and compat <= 17
            and 40 <= reservations < total and reservations % 8 == 0
            and 40 <= structure <= total - stsize and structure % 4 == 0
            and 40 <= strings <= total - strsize, "dtb_format", "DTB 標頭、版本或長度不符")
    require(not (structure <= reservations < structure + stsize)
            and not (strings <= reservations < strings + strsize),
            "dtb_memreserve", "DTB 保留表與其他資料區重疊")
    limit = min(offset for offset in (structure, strings, total) if offset > reservations)
    memreserve, cursor = [], reservations
    while True:
        require(cursor + 16 <= limit, "dtb_memreserve", "DTB 保留表截斷或缺少結尾")
        start, size = struct.unpack_from(">QQ", data, cursor)
        cursor += 16
        if start == size == 0:
            break
        require(size > 0 and start + size <= 1 << 64 and len(memreserve) < 1024,
                "dtb_memreserve", "DTB 保留區長度無效、位址溢位或項目過多")
        memreserve.append({"start": start, "size": size})
    def get(node, prop=None, *, mode=None):
        argv = ["/usr/bin/fdtget", "-" + mode] if mode else ["/usr/bin/fdtget", "-t", "bx"]
        raw = evidence.run([*argv, path, node, *([] if prop is None else [prop])]).decode().split()
        return raw if mode else bytes(int(value, 16) for value in raw)

    def policy():
        kernel_config = evidence.manifest["checks"].get("kernel_config")
        require(kernel_config is not None, "kernel_config", "動態 CMA 缺少原配核心配置核對證據")
        kernel = evidence.manifest["checks"].get("kernel", {})
        require(kernel.get("kernel_config_verified") is True
                and kernel.get("embedded_config", {}).get("sha256") == kernel_config["sha256"],
                "kernel_config", "動態 CMA 必須由核心內嵌配置核對原配 config；僅版本相同不足以放行")
        return cma.kernel_policy(kernel_config["values"], evidence.manifest["kernel_release"], kernel_config["sha256"])

    reservations = cma.reserved_memory(get, get("/", mode="l"), policy)
    return {"model": model, "compatible": compatible, "memreserve": memreserve, **reservations}


def _bootargs(env, arch):
    console = env.get("console", "both")
    args = ["root=" + env["rootdev"], "rootwait", "rootfstype=" + env.get("rootfstype", "ext4")]
    args += ["splash", "plymouth.ignore-serial-consoles"] if env.get("bootlogo", "false") == "true" else ["splash=verbose"]
    if arch == "arm32" and env.get("earlycon", "off") == "on":
        args.append("earlycon")
    if console != "display" or arch == "arm64":
        args.append("console=ttyS0,115200")
    if console != "serial":
        args.append("console=tty1")
    if arch == "arm32":
        args += ["hdmi.audio=EDID:0", "disp.screen0_output_mode=" + env.get("disp_mode", "1920x1080p60")]
    args += ["consoleblank=0", "loglevel=" + env.get("verbosity", "1"), "ubootpart=${partuuid}"]
    if arch == "arm32":
        args.append("ubootsource=${devtype}")
    args += ["usb-storage.quirks=" + env.get("usbstoragequirks", "")]
    args += env.get("extraargs", "").split() + env.get("extraboardargs", "").split()
    if arch == "arm32" and env.get("disp_mem_reserves", "off") == "off":
        args += ["sunxi_ve_mem_reserve=0", "sunxi_g2d_mem_reserve=0", "sunxi_fb_mem_reserve=16"]
    if env.get("docker_optimizations", "on") == "on":
        args.append("cgroup_enable=memory")
    return args


def _prepare(e, board, release):
    profile, script = _profile(e, board)
    m = e.manifest
    m.update(board=profile["board"], artifact_board=profile["artifact_board"], arch=profile["arch"], profile=profile)
    blobs = {}
    for role, path in (("release", "/etc/armbian-release"), ("env", "/boot/armbianEnv.txt"),
                       ("boot_cmd", "/boot/boot.cmd"), ("boot_scr", "/boot/boot.scr"),
                       ("kernel", "/boot/" + ("zImage" if profile["arch"] == "arm32" else "Image")),
                       ("initrd", "/boot/uInitrd"), ("initrd_raw", f"/boot/initrd.img-{release}"),
                       ("kernel_config", f"/boot/config-{release}"),
                       ("kernel_versioned", f"/boot/vmlinuz-{release}")):
        maximum = 1024**2 if role == "kernel_config" else MAX_TEXT if role in ("release", "env", "boot_cmd", "boot_scr") else MAX_FILE
        blobs[role] = e.get(path, role, required=True, maximum=maximum)
    if blobs["kernel_config"] is not None:
        e.check("kernel_config", lambda: cma.config_evidence(blobs["kernel_config"], profile["arch"]))
    custom = e.get("/boot/fixup.scr", "user_fixup", maximum=MAX_TEXT)
    if custom is not None:
        e.block("custom_fixup", "存在自訂 /boot/fixup.scr；保留證據但不執行、不省略")
        e.check("user_fixup_crc", lambda: bool(_legacy(custom, script=True)))
    legacy = profile["arch"] == "arm32" and e.get("/boot/.next", "next_marker", maximum=MAX_TEXT) is None
    if legacy:
        e.get("/boot/script.bin", "script_bin", required=True)
        e.block("legacy_fex", "未發現 .next；原腳本走 script.bin 舊式 FEX 分支，尚未實作")
    if blobs["boot_cmd"] is not None:
        e.check("boot_source", lambda: require(blobs["boot_cmd"] == script, "boot_script", "boot.cmd 與本倉已實作來源不同"))
    if blobs["boot_scr"] is not None:
        e.check("boot_scr", lambda: require(_legacy(blobs["boot_scr"], script=True) == blobs["boot_cmd"],
                                            "boot_script", "boot.scr 內容與 boot.cmd 不同"))
    if blobs["release"] is not None:
        metadata = e.check("release_parse", lambda: _env(blobs["release"], release=True))
        if metadata is not None:
            m["original_release"] = metadata
            expected = {"BOARD": profile["artifact_board"], "BOARDFAMILY": profile["family"],
                        "KERNEL_IMAGE_TYPE": "zImage" if profile["arch"] == "arm32" else "Image",
                        "INITRD_ARCH": "arm" if profile["arch"] == "arm32" else "arm64"}
            e.check("release_identity", lambda: require(all(metadata.get(k) == v for k, v in expected.items()),
                                                        "release_identity", "armbian-release 板型、家族或組件架構不符"))
    if blobs["kernel"] is not None:
        e.check("kernel", lambda: _kernel(blobs["kernel"], profile["arch"], release, blobs["kernel_config"]))
        e.check("kernel_alias", lambda: require(blobs["kernel"] == blobs["kernel_versioned"],
                                               "kernel_alias", "實際載入核心與指定版本檔案不同"))
    if blobs["initrd"] is not None:
        payload = e.check("initrd_crc", lambda: digest(_legacy(blobs["initrd"], arch=profile["arch"])))
        if payload is not None:
            raw = blobs["initrd"][64:]
            e.check("initrd_alias", lambda: require(raw == blobs["initrd_raw"], "initrd_alias", "uInitrd 與原 initrd.img 不同"))
            e.check("initramfs", lambda: _initramfs(raw, release))
    if blobs["env"] is None:
        return
    env = e.check("env_parse", lambda: _env(blobs["env"]))
    if env is None:
        return
    m["original_env"] = env
    root = re.fullmatch(r"UUID=([0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})", env.get("rootdev", ""))
    m["root_uuid"] = root[1] if root else None
    if e.check("environment", lambda: _check_env(env, profile)):
        m["bootargs_template"] = _bootargs(env, profile["arch"])
    # 按腳本搜尋次序讀取；明示錯板 fdtfile 仍擷取供稽核，絕不改選另一板而放行。
    fdtfile = env.get("fdtfile", profile["dtb"]).removeprefix("allwinner/")
    require(re.fullmatch(NAME + r"\.dtb", fdtfile), "dtb_name", "fdtfile 不是安全的 DTB 檔名")
    directory = env.get("fdtdir", "/boot/dtb" + ("/allwinner" if profile["arch"] == "arm64" else ""))
    _path(directory)
    candidates = [f"{directory}/{fdtfile}"]
    candidates += ([f"/boot/dtb/{fdtfile}"] if profile["arch"] == "arm64" else [f"/boot/dtb/allwinner/{fdtfile}"])
    # 原 U-Boot 的 deffdt_file 不在映像介面內，不能憑驗證表猜它的後備值。
    dtb_path = None
    for path in dict.fromkeys(candidates):
        if e.get(_path(path), "dtb") is not None:
            dtb_path = path
            break
    if dtb_path is None:
        e.block("dtb_missing", "已知 DTB 搜尋路徑均缺檔；不猜 U-Boot deffdt_file 的後備值")
        return
    m["dtb_resolution"] = {"path": dtb_path, "candidates": list(dict.fromkeys(candidates)),
                           "requires_uboot_default_fdtfile": "fdtfile" not in env,
                           "requires_default_fdtdir": "fdtdir" not in env}
    dtb_valid = e.check("dtb", lambda: _dtb(e, "dtb", profile))
    directory = str(Path(dtb_path).parent)
    prefix = env.get("overlay_prefix", "")
    require(re.fullmatch(NAME, prefix), "overlay_prefix", "overlay_prefix 不是安全名稱")
    overlays = []
    for key in ("overlays", "user_overlays"):
        names = e.check(key, lambda key=key: _overlay_names(env, key))
        if names is None:
            continue
        for name in names:
            path = (f"{directory}/overlay/{prefix}-{name}.dtbo" if key == "overlays"
                    else f"/boot/overlay-user/{name}.dtbo")
            role = f"overlay_{len(overlays):02d}"
            blob = e.get(_path(path), role, required=True, maximum=16 * 1024**2)
            overlays.append(role if blob is not None else None)
    fixup = e.get(_path(f"{directory}/overlay/{prefix}-fixup.scr"), "kernel_fixup", maximum=MAX_TEXT)
    if fixup is not None:
        def check_fixup():
            # boot.scr 固定以 -A arm 封裝；核心 fixup 使用核心建置的 -A $(ARCH)。
            payload = _legacy(fixup, script=True, arch=profile["arch"])
            require(prefix in FIXUPS, "fixup_unknown", "此家族 fixup 語意尚未實作")
            folder, sha = FIXUPS[prefix]
            source = e.source(f"patch/kernel/archive/sunxi-6.18/{folder}/{prefix}-fixup.scr-cmd")
            require(hashlib.sha256(source).hexdigest() == sha and payload == source,
                    "fixup_unknown", "fixup 不符合已審閱來源，不執行任意腳本")
            require(not any(key.startswith("param_") for key in env), "fixup_parameter", "fixup 參數尚未實作")
            pwm = {"sun8i-h3": "pwm", "sun50i-h616": "pwm34"}.get(prefix)
            require(pwm not in env.get("overlays", "").split(),
                    "fixup_pwm", "此 PWM fixup 會改寫 console，尚未實作此分支")
            return {"mode": "verified_no_dtb_change", "source_sha256": sha, "executed": False}
        e.check("fixup", check_fixup)
    if legacy or not dtb_valid or any(role is None for role in overlays):
        return
    m["overlay_order"] = overlays
    def apply_overlays():
        effective = "files/effective.dtb"
        if overlays:
            e.run(["/usr/bin/fdtoverlay", "-i", e.output / m["files"]["dtb"]["evidence_path"],
                   "-o", e.output / effective,
                   *[e.output / m["files"][role]["evidence_path"] for role in overlays]])
        else:
            e.save(effective, e.cache[dtb_path][0])
        data = (e.output / effective).read_bytes()
        m["files"]["effective_dtb"] = {"evidence_path": effective, **digest(data)}
        return _dtb(e, "effective_dtb", profile)
    e.check("overlay_application", apply_overlays)
    m["overlay_order"] = overlays


def prepare(read_file, *, board, kernel_release, output):
    """接受主代理的唯讀映像介面；成功或阻擋均回傳並保存 manifest。"""
    require(callable(read_file) and isinstance(board, str)
            and isinstance(kernel_release, str) and re.fullmatch(r"[A-Za-z0-9_.+~-]{1,128}", kernel_release),
            "arguments", "必須提供唯讀介面、明確板型及完整核心版本")
    manifest = {"schema": SCHEMA, "board": board, "kernel_release": kernel_release,
                "status": "blocked", "ready": False, "root_uuid": None, "hardware_validated": False,
                "ddr_validated": False, "boot_chain_validated": False,
                "source_image_verified": False, "files": {}, "sources": {}, "checks": {},
                "reads": [], "commands": [], "blockers": [],
                "scope": "僅原配組件離線準備；整張映像摘要與 symlink 安全由主代理核對，未授予部署、救援或實板資格"}
    evidence = _Evidence(output, read_file, manifest)
    try:
        try:
            _prepare(evidence, board, kernel_release)
        except (AllwinnerError, cma.CMAError) as exc:
            evidence.block(exc.code, str(exc))
        except Exception:
            evidence.block("preparation_failed", "準備遇到未完成的來源、讀取或解析條件；已擷取證據保留")
        required = ("environment", "release_identity", "boot_source", "boot_scr", "kernel", "kernel_alias", "kernel_config",
                    "initrd_crc", "initrd_alias", "initramfs", "dtb", "overlay_application")
        if not all(key in manifest["checks"] for key in required):
            evidence.block("incomplete", "必要核對未完整通過，禁止產生可引導配置")
        if not manifest["blockers"]:
            manifest.update(status="prepared", ready=True)
        evidence.save("manifest.json", _json(manifest))
        os.fsync(evidence.fd)
    finally:
        os.close(evidence.fd)
    return manifest


def _empty_marker(path):
    """共用組件讀取器拒絕空檔；僅為 .next 的存在語意補上唯讀核對。"""
    path = Path(path).absolute()
    require(".." not in path.parts, "evidence_path", "證據路徑不得含上層跳轉")
    fd = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        for part in path.parts[1:-1]:
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        marker = os.open(path.name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
        try:
            info = os.fstat(marker)
            require(stat.S_ISREG(info.st_mode) and info.st_size == 0,
                    "evidence_changed", "空 .next 證據已變動或不是一般檔案")
        finally:
            os.close(marker)
    finally:
        os.close(fd)
    return digest(b"")


def _verify_memory_evidence(manifest, artifact_root):
    """重驗核心、原配配置與兩份 DTB，防止修改摘要與核對欄位繞過 CMA。"""
    with safe.open_root(artifact_root) as root, tempfile.TemporaryDirectory(prefix="bpi-memory-") as temporary:
        checked = {"files": {}, "commands": [], "checks": {}, "sources": {},
                   "overlay_order": manifest.get("overlay_order", []), "kernel_release": manifest["kernel_release"]}
        evidence = _Evidence(Path(temporary) / "verify", None, checked)
        kernel_config = None
        try:
            env_record = manifest["files"]["env"]
            actual, blob = safe.fingerprint(root, env_record["evidence_path"], limit=MAX_TEXT, keep=True)
            require(actual == {key: env_record[key] for key in ("bytes", "sha256")},
                    "evidence_changed", "原始 overlay 環境證據已變動")
            env = _env(blob)
            _check_env(env, manifest["profile"])
            require(env == manifest["original_env"], "overlay_evidence", "overlay 環境核對結果與原始內容不同")
            paths = _overlay_paths(env, manifest["files"]["dtb"]["path"])
            require(checked["overlay_order"] == [f"overlay_{i:02d}" for i in range(len(paths))],
                    "overlay_evidence", "overlay 次序與原始環境啟用項目不同")
            for role, path in zip(checked["overlay_order"], paths):
                record = manifest["files"][role]
                require(record.get("path") == path, "overlay_evidence", "overlay 路徑與原始環境不同")
                actual, blob = safe.fingerprint(root, record["evidence_path"], limit=MAX_FILE, keep=True)
                require(actual == {key: record[key] for key in ("bytes", "sha256")},
                        "evidence_changed", "型號核對使用的 overlay 已變動")
                name = f"files/{role}.dtbo"
                evidence.save(name, blob)
                checked["files"][role] = {**record, "evidence_path": name}
            for role in ("kernel_config", "kernel", "dtb", "effective_dtb"):
                require(role in manifest["files"], "memory_evidence", "記憶體核對缺少必要原配證據，須重新擷取")
                record = manifest["files"][role]
                actual, blob = safe.fingerprint(root, record["evidence_path"], limit=1024**2 if role == "kernel_config" else MAX_FILE, keep=True)
                require(actual == {key: record[key] for key in ("bytes", "sha256")}, "evidence_changed", "記憶體來源證據已變動")
                if role == "kernel_config":
                    kernel_config = blob
                    result = cma.config_evidence(blob, manifest["arch"])
                    checked["checks"][role] = result
                    require(result == manifest["checks"].get(role), "memory_evidence", "原配核心配置核對結果已變動")
                elif role == "kernel":
                    result = _kernel(blob, manifest["arch"], manifest["kernel_release"], kernel_config)
                    checked["checks"][role] = result
                    require(result == manifest["checks"].get(role), "memory_evidence", "核心格式／版本／內嵌配置核對結果已變動")
                else:
                    name = "files/" + role + ".dtb"
                    if role == "effective_dtb":
                        base = evidence.output / checked["files"]["dtb"]["evidence_path"]
                        rebuilt = evidence.output / "files/rebuilt.dtb"
                        if paths:
                            evidence.run(["/usr/bin/fdtoverlay", "-i", base, "-o", rebuilt,
                                          *[evidence.output / checked["files"][key]["evidence_path"]
                                            for key in checked["overlay_order"]]])
                        else:
                            rebuilt = base
                        require(rebuilt.read_bytes() == blob, "overlay_evidence",
                                "有效 DTB 與原始 DTB 按原環境套用 overlay 的結果不同")
                    evidence.save(name, blob)
                    checked["files"][role] = {"evidence_path": name}
                    result = _dtb(evidence, role, manifest["profile"])
                    stage = "dtb" if role == "dtb" else "overlay_application"
                    require(result == manifest["checks"].get(stage), "memory_evidence", "DTB 保留區／CMA 核對結果與原配證據不同")
        except cma.CMAError as exc:
            raise AllwinnerError(exc.code, str(exc)) from exc
        finally:
            os.close(evidence.fd)


def build_uboot_config(manifest, *, template, artifact_root):
    """綁定外部已核定範本並執行共用雙重核對；只回傳配置，不接 UART。"""
    require(manifest.get("schema") == SCHEMA and manifest.get("status") == "prepared"
            and manifest.get("ready") is True and manifest.get("hardware_validated") is False
            and manifest.get("blockers") == [], "not_prepared", "組件準備未完整通過")
    actual, _ = uboot._read_regular(Path(artifact_root) / "manifest.json", 2 * 1024**2)
    require(actual == digest(_json(manifest)), "manifest_changed", "傳入 manifest 與持久證據不同")
    for role, record in manifest["files"].items():
        path = Path(artifact_root) / record["evidence_path"]
        if role == "next_marker" and record["bytes"] == 0:
            actual = _empty_marker(path)
        else:
            actual, _ = uboot._read_regular(path, MAX_FILE)
        require(actual == {key: record[key] for key in ("bytes", "sha256")}, "evidence_changed", "原配組件證據已變動")
    _verify_memory_evidence(manifest, artifact_root)
    config = copy.deepcopy(template)
    cma_approval = config.pop("allwinner_cma", None)
    require(config.get("arch") == manifest["arch"] and config.get("kernel_release") == manifest["kernel_release"],
            "template_identity", "外部範本的架構或核心版本與組件不同")
    args = config.get("bootargs", [])
    require(type(args) is list and all(isinstance(arg, str) for arg in args), "template_bootargs", "外部範本須明示 bootargs 陣列")
    partuuid = [arg for arg in args if arg.startswith("ubootpart=")]
    require(len(partuuid) == 1 and re.fullmatch(r"ubootpart=(?:[0-9a-f]{8}-[0-9a-f]{2}|[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})", partuuid[0]),
            "template_bootargs", "外部範本須核定唯一原引導分割 PARTUUID，不從載入媒體猜測")
    expected = [partuuid[0] if arg == "ubootpart=${partuuid}" else
                "ubootsource=" + str(config.get("source", {}).get("type")) if arg == "ubootsource=${devtype}" else arg
                for arg in manifest["bootargs_template"]]
    require(args == expected, "template_bootargs", "外部範本 bootargs 與原環境展開結果不同；不靜默改寫")
    for role, evidence_role, fmt in (("kernel", "kernel", "zImage" if manifest["arch"] == "arm32" else "Image"),
                                     ("initrd", "initrd", "legacy"), ("dtb", "effective_dtb", "dtb")):
        require(isinstance(config.get("files"), dict) and isinstance(config["files"].get(role), dict),
                "template_files", "外部範本缺少原配組件的 RAM 載入配置")
        record = manifest["files"][evidence_role]
        config["files"][role].update(path=record["evidence_path"], format=fmt,
                                    bytes=record["bytes"], sha256=record["sha256"])
    config = uboot.validate_config(config)
    for stage in ("dtb", "overlay_application"):
        checked = manifest.get("checks", {}).get(stage, {})
        require(all(type(checked.get(key)) is list for key in ("memreserve", "reserved_memory", "dynamic_cma")),
                "dtb_memreserve", "缺少 DTB 保留區／CMA 核對證據，須重新執行 snapshot replay")
        reservations = checked["memreserve"] + checked["reserved_memory"]
        for span in reservations:
            require(type(span) is dict and set(span) == {"start", "size"}
                    and type(span["start"]) is int and type(span["size"]) is int
                    and span["start"] >= 0 and span["size"] > 0,
                    "dtb_memreserve", "DTB 保留區證據格式無效")
            require(any(area["start"] <= span["start"]
                        and span["start"] + span["size"] <= area["start"] + area["size"]
                        for area in config["ram"]["reserved"]),
                    "dtb_memreserve", f"{stage}：DTB 保留區未完整包含於外部已核定 ram.reserved")
    requirements = manifest["checks"]["overlay_application"]["dynamic_cma"]
    require("kernel_config" in manifest["files"] and "kernel_config" in manifest["checks"],
            "kernel_config", "缺少原配核心配置證據，須重新擷取並重播")
    try:
        cma.validate(config, requirements, cma_approval,
                     config_sha256=manifest["files"]["kernel_config"]["sha256"],
                     dtb_sha256=manifest["files"]["effective_dtb"]["sha256"])
    except cma.CMAError as exc:
        raise AllwinnerError(exc.code, str(exc)) from exc
    uboot.validate_artifacts(config, artifact_root)
    return config
