#!/usr/bin/env python3
"""建立獨立 SD GPT 映像與 Titan eMMC 套件；僅操作候選一般檔案。"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import struct
import subprocess
import sys
import uuid
import zipfile
import zlib

REPO = Path(__file__).resolve().parents[1]
MIB = 1024 * 1024
DTBS = {"bpi-cm6": "k1-x_bpi_cm6.dtb", "bpi-f3": "k1-bananapi-f3.dtb"}
PARTS = [("fsbl", 128*1024, 256*1024, "factory/FSBL.bin"),
         ("env", 384*1024, 64*1024, "env.bin"),
         ("opensbi", MIB, MIB, "fw_dynamic.itb"),
         ("uboot", 2*MIB, 2*MIB, "u-boot.itb"),
         ("bootfs", 4*MIB, 256*MIB, "bootfs.ext4"),
         ("rootfs", 260*MIB, None, "rootfs.ext4")]


def digest(path):
    with Path(path).open("rb") as f:
        return digest_stream(f)


def digest_stream(stream, count=None):
    h = hashlib.sha256()
    left = count
    while left is None or left > 0:
        block = stream.read(8*MIB if left is None else min(left, 8*MIB))
        if not block:
            if left not in (None, 0):
                raise ValueError("映像內容過短")
            break
        h.update(block)
        if left is not None:
            left -= len(block)
    return h.hexdigest()


def run(argv, codes=(0,), **kw):
    r = subprocess.run(list(map(str, argv)), **kw)
    if r.returncode not in codes:
        raise RuntimeError(f"命令失敗：{argv[0]}，退出碼 {r.returncode}")
    return r


def regular(path):
    path = Path(path)
    if path.is_symlink() or not stat.S_ISREG(path.stat().st_mode):
        raise ValueError(f"只允許一般檔案：{path}")
    return path


def child_file(base, name):
    name = str(name)
    relative = Path(name)
    if not name or "\\" in name or relative.is_absolute() or ".." in relative.parts:
        raise ValueError("檔案路徑超出指定目錄")
    path = Path(base) / relative
    if not path.resolve().is_relative_to(Path(base).resolve()):
        raise ValueError("檔案連結超出指定目錄")
    return regular(path)


def write(path, content, mode=0o644):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    path.chmod(mode)


def extlinux(board, root_uuid):
    if board not in DTBS:
        raise ValueError("不支援的板型")
    root_uuid = str(uuid.UUID(root_uuid))
    return ("DEFAULT armbian\nTIMEOUT 20\nLABEL armbian\n"
            "  KERNEL /Image\n  INITRD /uInitrd\n"
            f"  FDT /dtb/spacemit/{DTBS[board]}\n"
            f"  APPEND root=UUID={root_uuid} rootwait rootfstype=ext4 rw "
            "earlycon=sbi console=tty1 console=ttyS0,115200 loglevel=4 "
            "fsck.repair=yes net.ifnames=0\n")


def layout(root_size, emmc=False):
    if root_size <= 0 or root_size % 4096:
        raise ValueError("根分區大小必須為正數且按 4096 位元組對齊")
    result = json.loads((REPO / "config/spacemit-k1-vendor/partition_universal.json").read_text())
    result["partitions"][0]["image"] = (
        "factory/bootinfo_emmc.bin" if emmc else "factory/bootinfo_sd.bin")
    return result


def check_payloads(directory):
    for _, _, maximum, name in PARTS:
        size = regular(directory / name).stat().st_size
        if not size or (maximum and size > maximum):
            raise ValueError(f"載荷超過分區或為空：{name}")
    for name in ("bootinfo_sd.bin", "bootinfo_emmc.bin"):
        data = regular(directory / "factory" / name).read_bytes()
        if len(data) != 80:
            raise ValueError("開機資訊必須為 80 位元組，不可覆蓋 GPT 保護 MBR")
        if struct.unpack_from("<I", data)[0] != 0xB00714F0 or zlib.crc32(data[:64]) != struct.unpack_from("<I", data, 64)[0]:
            raise ValueError("開機資訊標頭或 CRC32 不符")
    env = (directory / "env.bin").read_bytes()
    if len(env) != 16384 or zlib.crc32(env[4:]) != struct.unpack_from("<I", env)[0]:
        raise ValueError("官方環境映像長度或 CRC32 不符")


def source_reference(reference):
    record = json.loads(child_file(reference, "retrieval.json").read_text())
    config = REPO / "config/spacemit-k1-vendor"
    lock_path = child_file(config, "sources.lock.json")
    lock = json.loads(lock_path.read_text())
    for key in ("url", "archive_bytes"):
        if record[key] != lock[key]:
            raise ValueError("參考封裝來源與倉內來源鎖不符")
    entries = {item["path"]: item for item in record["files"]}
    if len(entries) != len(record["files"]) or set(entries) != {item["path"] for item in lock["files"]}:
        raise ValueError("參考元件清單有重複、缺少或額外項目")
    for item in lock["files"]:
        for key in ("size", "sha256", "crc32", "header_offset", "compressed_size"):
            if entries[item["path"]][key] != item[key]:
                raise ValueError(f"參考元件紀錄與來源鎖不符：{item['path']}")
        path = child_file(reference, item["path"])
        if path.stat().st_size != item["size"] or digest(path) != item["sha256"]:
            raise ValueError(f"參考元件內容與來源鎖不符：{item['path']}")
    for item in lock.get("local_templates", []):
        if digest(child_file(config, item["path"])) != item["sha256"]:
            raise ValueError(f"官方格式範本雜湊不符：{item['path']}")
    record["sources_lock_sha256"] = digest(lock_path)
    return record


def filesystem_uuid(stream, offset=0):
    stream.seek(offset + 1024)
    superblock = stream.read(120)
    if len(superblock) != 120 or superblock[56:58] != b"\x53\xef":
        raise ValueError("找不到有效的 ext4 超級區塊")
    return str(uuid.UUID(bytes=superblock[104:120]))


def normalize_kernel(boot):
    """保留可由 bootm 啟動的 FIT；gzip 核心轉為 booti 可讀的 Image。"""
    image = boot / "Image"
    with image.open("rb") as stream:
        compressed = stream.read(2) == b"\x1f\x8b"
    if compressed:
        temporary = boot / "Image.vendor-new"
        with gzip.open(image, "rb") as source, temporary.open("xb") as destination:
            shutil.copyfileobj(source, destination)
        image.unlink()
        temporary.rename(image)
        image.chmod(0o644)
    with image.open("rb") as stream:
        header = stream.read(64)
    kind = "raw"
    if header[:4] == b"\xd0\x0d\xfe\xed":
        details = subprocess.check_output(["dumpimage", "-l", str(image)], text=True)
        if "FIT description:" not in details or "Architecture: RISC-V" not in details or "Type:         Kernel Image" not in details:
            raise ValueError("FIT 不是合法的 RISC-V 核心")
        kind = "fit"
    elif len(header) != 64 or header[56:60] != b"RSC\x05":
        raise ValueError("核心不是合法的 RISC-V 開機 Image")
    if image.stat().st_size > 0x0c200000 - 0x08000000:
        raise ValueError("核心超過已驗證的官方載入記憶體區間")
    return {"source_was_gzip": compressed, "format": kind, "image_size": image.stat().st_size,
            "image_sha256": digest(image)}


def verify_boot_contract(payload, board, root_uuid, boot_uuid):
    def content(filesystem, name):
        return subprocess.check_output(["debugfs", "-R", "cat " + name,
                                        str(payload / filesystem)], stderr=subprocess.DEVNULL, text=True)
    expected = extlinux(board, root_uuid)
    if content("bootfs.ext4", "/extlinux/extlinux.conf") != expected:
        raise ValueError("實際 bootfs 開機選項與板型或根分區不符")
    environment = content("bootfs.ext4", "/env_k1-x.txt")
    if "bootcmd=sysboot ${bootfs_devname} ${boot_devnum}:${bootfs_part} any ${pxefile_addr_r} /extlinux/extlinux.conf" not in environment:
        raise ValueError("bootfs 缺少官方 U-Boot 的明確開機入口")
    fstab = content("rootfs.ext4", "/etc/fstab")
    for value, point in ((root_uuid, "/"), (boot_uuid, "/boot")):
        matches = [line.split() for line in fstab.splitlines() if not line.startswith("#") and len(line.split()) > 2 and line.split()[1] == point]
        if len(matches) != 1 or matches[0][:3] != ["UUID=" + value, point, "ext4"]:
            raise ValueError("實際根檔案系統掛載設定不符：" + point)
    marker = json.loads(content("rootfs.ext4", "/etc/bpi-k1-vendor.json"))
    if (marker["board"], marker["root_uuid"], marker["boot_uuid"]) != (board, root_uuid, boot_uuid):
        raise ValueError("根檔案系統內的媒體身分不符")
    return {"status": "passed", "scope": "bootfs-extlinux-env-rootfs-fstab-marker"}


def verify_sd_image(target, payload):
    """逐一核對實際 GPT、保護 MBR 與分區載荷，不依賴建立命令成功碼。"""
    total = regular(target).stat().st_size
    with target.open("rb") as stream:
        mbr = stream.read(512)
        if mbr[:80] != (payload / "factory/bootinfo_sd.bin").read_bytes() or mbr[450] != 0xEE or mbr[510:] != b"\x55\xaa":
            raise ValueError("SD 開機資訊或 GPT 保護 MBR 不符")
        headers = []
        for lba in (1, total // 512 - 1):
            stream.seek(lba * 512)
            data = bytearray(stream.read(512))
            if data[:8] != b"EFI PART":
                raise ValueError("SD GPT 標頭不存在")
            length, crc = struct.unpack_from("<II", data, 12)
            if not 92 <= length <= 512:
                raise ValueError("SD GPT 標頭長度不合法")
            data[16:20] = b"\0" * 4
            if zlib.crc32(data[:length]) != crc:
                raise ValueError("SD GPT 標頭 CRC32 不符")
            if struct.unpack_from("<Q", data, 24)[0] != lba:
                raise ValueError("SD GPT 標頭位置不符")
            entries_lba, count, entry_size, table_crc = struct.unpack_from("<QIII", data, 72)
            if entry_size != 128 or count < len(PARTS) or count > 4096:
                raise ValueError("SD GPT 分區表大小不合法")
            stream.seek(entries_lba * 512)
            table = stream.read(count * entry_size)
            if len(table) != count * entry_size or zlib.crc32(table) != table_crc:
                raise ValueError("SD GPT 分區表 CRC32 不符")
            headers.append((data, table))
        if headers[0][1] != headers[1][1] or struct.unpack_from("<Q", headers[0][0], 32)[0] != total // 512 - 1 or struct.unpack_from("<Q", headers[1][0], 32)[0] != 1:
            raise ValueError("SD GPT 主副本不一致")
        table = headers[0][1]
        for index, (name, offset, maximum, source) in enumerate(PARTS):
            size = maximum or (payload / source).stat().st_size
            entry = table[index*128:(index+1)*128]
            if struct.unpack_from("<QQ", entry, 32) != (offset//512, (offset+size)//512-1) or entry[56:128].decode("utf-16-le").rstrip("\0") != name:
                raise ValueError(f"SD 分區界線或名稱不符：{name}")
            stream.seek(offset)
            if digest_stream(stream, (payload / source).stat().st_size) != digest(payload / source):
                raise ValueError(f"SD 分區載荷內容不符：{name}")
        if any(table[len(PARTS)*128:]):
            raise ValueError("SD 映像含額外 GPT 分區")
    return {"status": "passed", "scope": "gpt-crc-mbr-payload-ranges"}


def make_sd(payload, target, identity):
    check_payloads(payload)
    size = (payload / "rootfs.ext4").stat().st_size
    total = 260*MIB + size + MIB
    with target.open("xb") as f:
        f.truncate(total)
    seed = json.dumps(identity, sort_keys=True)
    argv = ["sgdisk", "--clear", "--set-alignment=1", "--disk-guid=" + str(uuid.uuid5(uuid.NAMESPACE_URL, seed))]
    for n, (name, offset, maximum, _) in enumerate(PARTS, 1):
        length = size if maximum is None else maximum
        argv += [f"--new={n}:{offset//512}:{(offset+length)//512-1}",
                 f"--change-name={n}:{name}", f"--typecode={n}:8300",
                 f"--partition-guid={n}:" + str(uuid.uuid5(uuid.NAMESPACE_URL, seed + name))]
    run([*argv, target], stdout=subprocess.DEVNULL)
    with target.open("r+b") as dst:
        dst.write((payload / "factory/bootinfo_sd.bin").read_bytes())
        for _, offset, _, name in PARTS:
            dst.seek(offset)
            with (payload / name).open("rb") as src:
                shutil.copyfileobj(src, dst, 8*MIB)
        dst.flush()
        os.fsync(dst.fileno())
    run(["sgdisk", "--verify", target])
    verify_sd_image(target, payload)
    return total


def archive(target, entries):
    with zipfile.ZipFile(target, "x", compression=zipfile.ZIP_DEFLATED,
                         compresslevel=3, allowZip64=True) as z:
        for name, path in sorted(entries.items()):
            z.write(path, name)


def verify(manifest_path):
    manifest_path = Path(manifest_path)
    m = json.loads(regular(manifest_path).read_text())
    base = manifest_path.parent
    artifact = child_file(base, m["artifact"]["name"])
    if digest(artifact) != m["artifact"]["sha256"]:
        raise ValueError("交付壓縮檔雜湊不符")
    with zipfile.ZipFile(artifact) as z:
        names = z.namelist()
        expected = m["archive_members"]
        if len(names) != len(set(names)) or set(names) != set(expected):
            raise ValueError("壓縮套件有重複、缺少或多餘檔案")
        for name in names:
            if not name or "\\" in name or Path(name).is_absolute() or ".." in Path(name).parts:
                raise ValueError("不安全的壓縮套件路徑")
            i = z.getinfo(name)
            if i.file_size != expected[name]["size"]:
                raise ValueError(f"元件大小不符：{name}")
            with z.open(name) as f:
                if digest_stream(f) != expected[name]["sha256"]:
                    raise ValueError(f"元件雜湊不符：{name}")
        if "root_uuid" in m and "boot_uuid" in m:
            if m["root_uuid"] == m["boot_uuid"]:
                raise ValueError("根檔案系統與開機分區 UUID 重複")
            for part, offset in (("boot", 4*MIB), ("root", 260*MIB)):
                member = next(iter(expected)) if m["storage"] == "sd" else part + "fs.ext4"
                with z.open(member) as stream:
                    actual = filesystem_uuid(stream, offset if m["storage"] == "sd" else 0)
                if actual != m[part + "_uuid"]:
                    raise ValueError(f"封裝內檔案系統 UUID 不符：{part}")
    return {"status": "passed", "scope": "offline-archive-integrity",
            "board": m["board"], "storage": m["storage"], "hardware_validation": "pending"}


def build(args):
    if os.geteuid() != 0:
        raise ValueError("需要 sudo，在隔離掛載中產生媒體專屬檔案系統")
    if not args.inside:
        os.execvp("unshare", ["unshare", "--mount", "--propagation", "private",
                             sys.executable, str(Path(__file__).resolve()), *sys.argv[1:], "--inside"])
    prepared = args.prepared.resolve()
    prep = json.loads((prepared / "preparation.json").read_text())
    if prep["status"] != "complete" or prep["identity"]["board"] != args.board:
        raise ValueError("根檔案系統尚未完成或板型不符")
    preflight = json.loads((prepared / "acceleration-preflight.json").read_text())
    if not preflight.get("passed") or preflight.get("board") != args.board or preflight.get("stage") != "installed":
        raise ValueError("已安裝加速配套尚未通過離線預檢")
    acceleration_lock = REPO / "config/spacemit-k1-acceleration/noble.lock.json"
    if prep["identity"]["acceleration_lock_sha256"] != digest(regular(acceleration_lock)):
        raise ValueError("準備映像使用的加速來源鎖已變更，必須重新核對準備結果")
    if digest(regular(prepared / "rootfs.ext4")) != prep["rootfs_sha256"]:
        raise ValueError("根檔案系統完成後遭到變更")
    reference = args.reference.resolve()
    reference_record = source_reference(reference)
    out = args.output.absolute()
    if out.exists():
        raise ValueError("交付目錄已存在，拒絕覆寫")
    out.mkdir(parents=True)
    payload = out / "payload"
    (payload / "factory").mkdir(parents=True)
    files = ["factory/FSBL.bin", "factory/bootinfo_sd.bin", "factory/bootinfo_emmc.bin",
             "fw_dynamic.itb", "u-boot.itb", "env.bin"]
    for name in files:
        shutil.copyfile(regular(reference / name), payload / name)
    # source_reference 已以倉內固定來源清單核對實際內容。
    reference_hashes = {name: digest(reference / name) for name in files}
    run(["cp", "--sparse=always", "--reflink=auto", prepared / "rootfs.ext4", payload / "rootfs.ext4"])
    identity = {**prep["identity"], "storage": args.storage, "layout_version": "bianbu-v2.3",
                "reference_payloads": reference_hashes,
                "sources_lock_sha256": reference_record["sources_lock_sha256"]}
    root_uuid = str(uuid.uuid5(uuid.NAMESPACE_URL, json.dumps(identity, sort_keys=True) + ":root"))
    boot_uuid = str(uuid.uuid5(uuid.NAMESPACE_URL, json.dumps(identity, sort_keys=True) + ":boot"))
    run(["tune2fs", "-U", root_uuid, "-L", "armbian-root", payload / "rootfs.ext4"], stdout=subprocess.DEVNULL)
    boot = out / "boot-tree"
    run(["cp", "-a", prepared / "boot-tree", boot])
    for path in boot.rglob("*"):
        if path.is_symlink() and str(path.readlink()).startswith("/boot/"):
            target = str(path.readlink())[6:]
            path.unlink()
            path.symlink_to(os.path.relpath(boot / target, path.parent))
    for name in ("Image", "uInitrd", "dtb/spacemit/" + DTBS[args.board]):
        if not (boot / name).is_file():
            raise ValueError(f"開機元件不存在：{name}")
    kernel_record = normalize_kernel(boot)
    write(boot / "extlinux/extlinux.conf", extlinux(args.board, root_uuid))
    write(boot / "env_k1-x.txt", "kernel_addr_r=0x08000000\nfdt_addr_r=0x31000000\n"
          "ramdisk_addr_r=0x21000000\npxefile_addr_r=0x0c200000\n"
          "bootcmd=sysboot ${bootfs_devname} ${boot_devnum}:${bootfs_part} any ${pxefile_addr_r} /extlinux/extlinux.conf\n")
    with (payload / "bootfs.ext4").open("xb") as f:
        f.truncate(256*MIB)
    run(["mkfs.ext4", "-q", "-F", "-U", boot_uuid, "-L", "bootfs", "-d", boot, payload / "bootfs.ext4"])
    mount = out / "mount"
    mount.mkdir()
    try:
        run(["mount", "-o", "loop,nodev,nosuid", payload / "rootfs.ext4", mount])
        fstab = mount / "etc/fstab"
        old = fstab.read_text()
        kept = []
        for line in old.splitlines():
            f = line.split()
            if len(f) >= 2 and not line.lstrip().startswith("#") and f[1] in ("/", "/boot"):
                continue
            kept.append(line)
        kept += [f"UUID={root_uuid} / ext4 defaults,noatime,errors=remount-ro 0 1",
                 f"UUID={boot_uuid} /boot ext4 defaults,noatime 0 2"]
        write(fstab, "\n".join(kept) + "\n")
        marker = {**identity, "root_uuid": root_uuid, "boot_uuid": boot_uuid,
                  "dtb": DTBS[args.board], "hardware_validation": "pending"}
        write(mount / "etc/bpi-k1-vendor.json", json.dumps(marker, ensure_ascii=False, indent=2) + "\n")
        grow_tool = mount / "usr/local/sbin/bpi_k1_grow_rootfs.py"
        shutil.copyfile(REPO / "tools/bpi_k1_grow_rootfs.py", grow_tool)
        grow_tool.chmod(0o755)
        (mount / "var/lib/bpi-k1-vendor").mkdir(parents=True, exist_ok=True)
        write(mount / "etc/systemd/system/bpi-k1-grow-rootfs.service",
              "[Unit]\nDescription=擴大已核對的 K1 官方 GPT 根分區\n"
              "DefaultDependencies=no\nAfter=sysinit.target local-fs.target\nBefore=basic.target\n"
              "ConditionPathExists=/etc/bpi-k1-vendor.json\n"
              "ConditionPathExists=!/var/lib/bpi-k1-vendor/rootfs-expanded\n"
              "[Service]\nType=oneshot\nRemainAfterExit=yes\nTimeoutStartSec=6min\n"
              "ExecStart=/usr/local/sbin/bpi_k1_grow_rootfs.py\n"
              "ExecStartPost=/usr/bin/touch /var/lib/bpi-k1-vendor/rootfs-expanded\n"
              "[Install]\nWantedBy=basic.target\n")
        enabled = mount / "etc/systemd/system/basic.target.wants/bpi-k1-grow-rootfs.service"
        enabled.parent.mkdir(parents=True, exist_ok=True)
        enabled.symlink_to("../bpi-k1-grow-rootfs.service")
        # 舊 /boot 留在原始準備映像，新候選則由獨立 bootfs 掛載。
        for child in (mount / "boot").iterdir():
            if child.is_symlink() or child.is_file():
                child.unlink()
            else:
                shutil.rmtree(child)
        hook = '#!/usr/bin/python3\n"""更新核心後維持官方分區配置的開機選項。"""\nimport gzip,json,os,shutil\nfrom pathlib import Path\nm=json.loads(Path("/etc/bpi-k1-vendor.json").read_text())\n'
        hook += 'source=Path("/boot/vmlinuz")\nif not source.is_file():\n    raise SystemExit("找不到套件更新後的核心")\nwith source.open("rb") as f:\n    compressed=f.read(2)==b"\\x1f\\x8b"\ntarget=Path("/boot/Image.vendor-new")\nwith (gzip.open(source,"rb") if compressed else source.open("rb")) as src, target.open("wb") as dst:\n    shutil.copyfileobj(src,dst)\ndata=target.read_bytes()\nexpected={"bpi-cm6":b"23.2@6460340","bpi-f3":b"24.2@6603887"}[m["board"]]\nif len(data)>0x04200000 or (data[:4]!=b"\\xd0\\x0d\\xfe\\xed" and data[56:60]!=b"RSC\\x05") or expected not in data:\n    target.unlink()\n    raise SystemExit("核心格式、大小或 GPU 配套已變更，須重新驗證整套映像")\nos.replace(target,"/boot/Image")\n'
        hook += 'text="DEFAULT armbian\\nTIMEOUT 20\\nLABEL armbian\\n  KERNEL /Image\\n  INITRD /uInitrd\\n  FDT /dtb/spacemit/"+m["dtb"]+"\\n  APPEND root=UUID="+m["root_uuid"]+" rootwait rootfstype=ext4 rw earlycon=sbi console=tty1 console=ttyS0,115200 loglevel=4 fsck.repair=yes net.ifnames=0\\n"\n'
        hook += 'Path("/boot/extlinux").mkdir(exist_ok=True)\nPath("/boot/extlinux/extlinux.conf").write_text(text)\n'
        compile(hook, "zz-bpi-k1-vendor", "exec")
        write(mount / "etc/kernel/postinst.d/zz-bpi-k1-vendor", hook, 0o755)
    finally:
        if os.path.ismount(mount):
            run(["umount", mount])
    for name in ("rootfs.ext4", "bootfs.ext4"):
        run(["e2fsck", "-fn", payload / name])
        with (payload / name).open("rb") as stream:
            if filesystem_uuid(stream) != (root_uuid if name == "rootfs.ext4" else boot_uuid):
                raise ValueError(f"建立的檔案系統 UUID 不符：{name}")
    boot_contract = verify_boot_contract(payload, args.board, root_uuid, boot_uuid)
    check_payloads(payload)
    layout_obj = layout((payload / "rootfs.ext4").stat().st_size, args.storage == "emmc")
    write(payload / "partition_universal.json", json.dumps(layout_obj, indent=2) + "\n")
    # 官方格式只提供區塊裝置表；eMMC 作業仍須依說明設定開機腳位並移除其他媒體。
    fastboot = (REPO / "config/spacemit-k1-vendor/fastboot.yaml").read_text()
    lines = []
    for line in fastboot.splitlines():
        line = line.split(" #", 1)[0]
        if "relate_partition:" in line:
            line = "      relate_partition: ['partition_universal.json']"
        if not line.lstrip().startswith("#"):
            lines.append(line)
    write(payload / "fastboot.yaml", "\n".join(lines) + "\n")
    stem = f"Armbian_Noble_{args.board}_gnome_{'vendor-sd' if args.storage == 'sd' else 'titan-emmc'}_20260916"
    manifest = {"schema_version": 1, "board": args.board, "release": "noble", "desktop": "gnome-wayland",
                "storage": args.storage, "identity": identity, "root_uuid": root_uuid, "boot_uuid": boot_uuid,
                "kernel": prep["kernel"], "hardware_validation": "pending", "archive_members": {},
                "boot_kernel": kernel_record,
                "boot_contract": boot_contract, "acceleration_preflight": preflight,
                "reference": reference_record}
    if args.storage == "sd":
        img = out / (stem + ".img")
        manifest["minimum_media_bytes"] = make_sd(payload, img, identity)
        manifest["sd_structure_validation"] = {"status": "passed", "scope": "gpt-crc-mbr-payload-ranges"}
        entries = {img.name: img}
        target = out / (stem + ".img.zip")
    else:
        manifest["minimum_media_bytes"] = 260*MIB + (payload / "rootfs.ext4").stat().st_size + MIB
        entries = {p.relative_to(payload).as_posix(): p for p in payload.rglob("*") if p.is_file()}
        target = out / (stem + ".zip")
    for name, path in entries.items():
        manifest["archive_members"][name] = {"size": path.stat().st_size, "sha256": digest(path)}
    archive(target, entries)
    artifact_hash = digest(target)
    manifest["artifact"] = {"name": target.name, "sha256": artifact_hash, "size": target.stat().st_size}
    write(out / (target.name + ".sha256"), artifact_hash + "  " + target.name + "\n")
    write(out / "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    result = verify(out / "manifest.json")
    write(out / "verification.json", json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(f"已產生並通過離線完整性驗證：{target}；實機驗證待回填。")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    b = sub.add_parser("build", help="建立單一媒體成品")
    b.add_argument("--board", choices=DTBS, required=True)
    b.add_argument("--storage", choices=("sd", "emmc"), required=True)
    b.add_argument("--prepared", type=Path, required=True)
    b.add_argument("--reference", type=Path, required=True)
    b.add_argument("--output", type=Path, required=True)
    b.add_argument("--inside", action="store_true", help=argparse.SUPPRESS)
    v = sub.add_parser("verify", help="重新核對發布套件與內部元件")
    v.add_argument("manifest", type=Path)
    a = p.parse_args()
    if a.command == "verify":
        print(json.dumps(verify(a.manifest), ensure_ascii=False, indent=2))
    else:
        build(a)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, RuntimeError, OSError, KeyError, zipfile.BadZipFile) as exc:
        print(f"封裝失敗：{exc}", file=sys.stderr)
        sys.exit(1)
