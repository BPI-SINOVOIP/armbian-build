#!/usr/bin/env python3
"""只對固定 R2 樣本建立環境路徑修正副本；不寫原映像或實體媒體。"""

import argparse
import hashlib
import os
from pathlib import Path
import re
import struct

import bpi_lab_disk as disk
import bpi_lab_image as image
import bpi_lab_prepare as preparation

SOURCE_SHA256 = "38a736cbc41c21cb7969e18d0f7954a5ea217af70f347beadb0883ef6e2d58e5"
RAW_SHA256 = "8f0335848b6868e89b2edc21600e26aa85d0c338637d1a62241128e40d3b4f6e"
DTB_SHA256 = "55151de1694bb279e759498eb5f86253e0e90700408044c546b4310a2a81c796"
KERNEL_SHA256 = "020f92c6a93f0f06f94963f9d535959885beaad49ffc716e480567c8f92b1bec"
RELEASE = "6.6.153-current-mt7623"
OLD_ENV = (b"verbosity=1\noverlay_prefix=mt7623\nfdtfile=mediatek/mt7623n-bananapi-bpi-r2\n"
           b"rootdev=UUID=48136f54-b977-4c35-a2a9-25267664570a\nrootfstype=ext4\n")
NAME = "Armbian-unofficial_26.11.0-trunk_Bananapir2_bookworm_current_6.6.153_minimal_f3-dtb-path.img"
require = image.require


def replacement(blob):
    require(blob == OLD_ENV, "只接受已核對固定樣本的完整原環境")
    value = blob.replace(b"fdtfile=mediatek/mt7623n-bananapi-bpi-r2\n",
                         b"fdtfile=mt7623n-bananapi-bpi-r2.dtb\n") + b"#F3\n\n"
    require(len(value) == len(blob), "修正須保持原 inode 長度")
    return value


def validate_environment_inode(description):
    require(re.search(rb"Type:\s+regular\s+Mode:\s+0600\s+Flags:\s+0x80000\b", description)
            and re.search(rb"Links:\s+1\b", description)
            and re.search(rb"\bSize:\s+141\b", description),
            "環境 inode 不是固定樣本的 0600 單連結一般 extents 檔案")


def copy_patched(source_fd, destination, size, offset, old, new, check):
    require(type(size) is int and type(offset) is int and len(old) == len(new) > 0
            and 0 <= offset < offset + len(old) <= size, "副本修改範圍無效")
    require(os.pread(source_fd, len(old), offset) == old, "檔案映射與原環境內容不符")
    before, after, position = hashlib.sha256(), hashlib.sha256(), 0
    while position < size:
        check()
        blob = os.pread(source_fd, min(image.CHUNK, size - position), position)
        require(blob, "原映像讀取截斷")
        before.update(blob)
        first, last = max(position, offset), min(position + len(blob), offset + len(old))
        if first < last:
            start, end = first - position, last - position
            blob = blob[:start] + new[first - offset:last - offset] + blob[end:]
        require(destination.write(blob) == len(blob), "副本寫入截斷")
        after.update(blob)
        position += len(blob)
    destination.flush()
    os.fsync(destination.fileno())
    return {"bytes": position, "original_sha256": before.hexdigest(), "sha256": after.hexdigest()}


def create_candidate(source, output):
    output = image.create_directory(output)
    target = output / NAME
    with disk.DiskReader(source, SOURCE_SHA256, output / "original-extraction", timeout=1800) as reader:
        require(reader.report["raw"]["sha256"] == RAW_SHA256, "固定原始解壓摘要不符")
        require(len(reader.volumes) == 1 and reader.boot_volume is reader.root_volume
                and reader.report["partition"]["table"] == "dos", "不接受其他分割配置")
        env = reader.read_file("/boot/armbianEnv.txt")
        new = replacement(env)
        dtb = reader.read_file("/boot/dtb/mt7623n-bananapi-bpi-r2.dtb")
        kernel = reader.read_file("/boot/zImage")
        require(image.digest(dtb)["sha256"] == DTB_SHA256
                and image.digest(kernel)["sha256"] == KERNEL_SHA256, "原核心或平鋪 DTB 不屬於已核對套件")
        unchanged = {path: image.digest(reader.read_file(path)) for path in (
            "/boot/zImage", "/boot/uInitrd", "/boot/boot.cmd", "/boot/boot.scr",
            "/boot/dtb/mt7623n-bananapi-bpi-r2.dtb", "/etc/armbian-release", "/etc/fstab")}
        volume = reader.root_volume
        fd = volume.part_stream.fileno()
        stat = volume._query("stat", "/boot/armbianEnv.txt")
        validate_environment_inode(stat)
        shift = struct.unpack("<I", os.pread(fd, 4, 1024 + 24))[0]
        require(0 <= shift <= 6, "ext 區塊大小無效")
        block_size = 1024 << shift
        require(len(env) <= block_size, "環境超出單一區塊")
        code, blob, error = image.bounded_run(
            ["/usr/sbin/debugfs", "-R", "bmap /boot/armbianEnv.txt 0", f"/proc/self/fd/{fd}"],
            timeout=90, stdout_limit=4096, pass_fds=(fd,))
        image.save(output, "bmap.stdout", blob)
        image.save(output, "bmap.stderr", error)
        require(code == 0 and re.fullmatch(rb"[1-9][0-9]*\n", blob)
                and re.fullmatch(rb"debugfs [^\n]+\n", error), "環境資料區塊無法唯一核對")
        inside = int(blob) * block_size
        partition = reader.report["partition"]
        require(inside + len(env) <= partition["bytes"], "環境資料區塊越界")
        offset = partition["start_lba"] * 512 + inside
        with image.safe.open_root(output) as root, image.safe.open_file(root, NAME, create=True) as out:
            raw = copy_patched(reader.part_stream.fileno(), out, reader.report["raw"]["bytes"],
                               offset, env, new, reader.check)
        require(raw["original_sha256"] == RAW_SHA256, "複製期間原始資料改變")
        image.save(output, "armbianEnv.original.txt", env)
        image.save(output, "armbianEnv.candidate.txt", new)
    with disk.DiskReader(target, raw["sha256"], output / "candidate-extraction", timeout=1800) as candidate:
        require(candidate.read_file("/boot/armbianEnv.txt") == new, "候選環境回讀不同")
        for path, wanted in unchanged.items():
            require(image.digest(candidate.read_file(path)) == wanted, "候選的原配組件變更：" + path)
        fd = candidate.root_volume.part_stream.fileno()
        code, stdout, stderr = image.bounded_run(
            ["/usr/sbin/e2fsck", "-f", "-n", f"/proc/self/fd/{fd}"],
            timeout=300, stdout_limit=1024**2, pass_fds=(fd,))
        image.save(output, "e2fsck.stdout", stdout)
        image.save(output, "e2fsck.stderr", stderr)
        require(code == 0, "候選檔案系統唯讀檢查未通過")
        manifest = preparation.prepare_family("mediatek", candidate.read_file, board="bpi-r2",
                                              kernel_release=RELEASE, output=output / "components")
        require(manifest["status"] == "prepared", "修正後家族組件仍未通過")
        root = preparation.root_binding(manifest, candidate)
        image.save_json(output, "family-result.json", manifest)
    code, stdout, stderr = image.bounded_run(["/usr/bin/xz", "--keep", "-T2", "-6", str(target)],
                                           timeout=1800, stdout_limit=4096)
    image.save(output, "xz.stderr", stderr)
    require(code == 0 and not stdout and not stderr, "候選壓縮失敗")
    with image.safe.open_root(output) as directory:
        compressed, _ = image.safe.fingerprint(directory, NAME + ".xz", limit=4 * 1024**3)
    result = {"schema": "bpi-r2-derived-candidate-v1", "hardware_validated": False,
              "rebuilt": False, "source_modified": False, "original_source_still_blocked": True,
              "source": {"path": str(Path(source).absolute()), "sha256": SOURCE_SHA256},
              "candidate": {"path": str(target) + ".xz", **compressed}, "raw": raw,
              "changed_range": {"offset": offset, "bytes": len(env), "before": image.digest(env),
                                "after": image.digest(new)},
              "unchanged_components": unchanged, "root_binding": root,
              "e2fsck_exit_code": 0, "family_prepared": True,
              "note": "只改固定副本內的環境資料位元組；以註解補齊原長度，inode、分割表及其餘原始位元組不變。不是重新編譯或實板結果。"}
    image.save(output, NAME + ".xz.sha", (compressed["sha256"] + "  " + NAME + ".xz\n").encode())
    image.save_json(output, "candidate.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description="固定 R2 樣本的 DTB 路徑衍生候選，不支援其他映像或媒體")
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    create_candidate(args.source, args.output)


if __name__ == "__main__":
    main()
