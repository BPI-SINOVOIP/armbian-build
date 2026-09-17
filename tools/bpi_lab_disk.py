#!/usr/bin/env python3
"""唯讀擷取 MBR／GPT 多分割映像，以 fstab 綁定 ext 根及選用的 FAT／ext 開機區。"""

from __future__ import annotations

import hashlib
import os
import re
import struct
import uuid

if __package__:
    from . import bpi_lab_image as image
else:
    import bpi_lab_image as image

require = image.require


def partitions(document, raw_path, size):
    """sfdisk 負責解析格式；此處核對範圍、唯一身分及禁止延伸分割。"""
    require(type(document) is dict and set(document) == {"partitiontable"}, "分割表 JSON 無效")
    table = document["partitiontable"]
    require(type(table) is dict and table.get("label") in ("dos", "gpt")
            and table.get("unit") == "sectors" and table.get("sectorsize") == 512
            and table.get("device") == raw_path, "只接受 512 位元組磁區的 MBR／GPT")
    rows = table.get("partitions")
    require(type(rows) is list and 1 <= len(rows) <= 32, "分割數量無效或超界")
    result = []
    for row in rows:
        require(type(row) is dict, "分割欄位不是物件")
        start, count, node = row.get("start"), row.get("size"), row.get("node")
        require(type(start) is int and type(count) is int and start > 0 and count > 0
                and (start + count) * 512 <= size, "分割範圍越界")
        require(type(node) is str and node.startswith(raw_path), "分割來源路徑不符")
        suffix = node[len(raw_path):].removeprefix("p")
        require(re.fullmatch(r"[1-9][0-9]*", suffix), "無法辨識分割編號")
        index = int(suffix)
        if table["label"] == "dos":
            require(index <= 4 and row.get("type", "").lower() not in ("5", "f", "85", "ee"),
                    "延伸、混合或保護 MBR 須另行核定")
            identifier = table.get("id", "")
            require(re.fullmatch(r"0x[0-9a-fA-F]{8}", identifier), "MBR 磁碟身分無效")
            partuuid = identifier[2:].lower() + f"-{index:02x}"
        else:
            partuuid = str(uuid.UUID(row["uuid"]))
        result.append({"index": index, "start_lba": start, "sectors": count,
                       "partuuid": partuuid, "type": row.get("type"), "table": table["label"]})
    require(len({x["index"] for x in result}) == len(result)
            and len({x["partuuid"] for x in result}) == len(result), "分割身分重複")
    ordered = sorted(result, key=lambda item: item["start_lba"])
    require(all(a["start_lba"] + a["sectors"] <= b["start_lba"]
                for a, b in zip(ordered, ordered[1:])), "分割範圍重疊")
    return result


def fstab_mounts(blob):
    require(len(blob) <= 65536, "fstab 超過上限")
    result = {}
    for line in blob.decode("utf-8").splitlines():
        line = line.partition("#")[0].strip()
        if not line:
            continue
        fields = line.split()
        require(4 <= len(fields) <= 6, "fstab 欄位不完整")
        source, target, kind = fields[:3]
        require(target not in result, "fstab 掛載點重複")
        require("\\" not in source + target, "此版不處理含跳脫字元的掛載路徑")
        require(target == "/" or (target.startswith("/") and all(
            part not in ("", ".", "..") for part in target[1:].split("/"))) or target == "none",
            "fstab 掛載點不是明確正規路徑")
        if target not in ("/", "/boot"):
            require(not target.startswith(("/boot/", "/usr", "/etc", "/lib")),
                    "開機必要路徑另有掛載，須另行適配")
            continue
        require(source.startswith(("UUID=", "PARTUUID=")), "根及開機區須以 UUID／PARTUUID 明示")
        require(kind in ("ext2", "ext3", "ext4", "vfat"), "掛載檔案系統未支援")
        result[target] = {"source": source, "filesystem": kind}
    require("/" in result and result["/"]["filesystem"] != "vfat", "缺少 ext 根掛載")
    return result


def matches(volume, source):
    kind, value = source.split("=", 1)
    actual = volume.filesystem_uuid if kind == "UUID" else volume.metadata["partuuid"]
    return value.lower() == actual.lower()


def protective_mbr(blob, size):
    require(len(blob) == 512 and blob[510:] == b"\x55\xaa", "GPT 缺少保護 MBR")
    entries = [blob[446 + i * 16:462 + i * 16] for i in range(4)]
    entries = [item for item in entries if item != bytes(16)]
    require(len(entries) == 1 and entries[0][0] == 0 and entries[0][4] == 0xee
            and struct.unpack_from("<II", entries[0], 8) == (1, min(size // 512 - 1, 0xffffffff)),
            "GPT 不是純保護 MBR，拒絕混合分割視圖")


class PartitionView(image.ImageReader):
    """唯讀分割副本；只借用父物件的已核對一般檔案描述符。"""

    def __init__(self, parent, metadata, filesystem):
        self.parent, self.metadata, self.filesystem = parent, metadata, filesystem
        super().__init__(parent.source, parent.expected, parent.output / f"volume-{metadata['index']:02d}",
                         max_raw_bytes=parent.maximum, timeout=max(1, parent.check()))
        self.deadline = parent.deadline

    def _extract(self):
        self.partition = self.output / "partition.tmp"
        offset, length = self.metadata["start_lba"] * 512, self.metadata["sectors"] * 512
        checksum = hashlib.sha256()
        with image.safe.open_file(self.output_fd, self.partition.name, create=True) as out:
            self.partition_created = True
            self.created_identity = image.identity(out)[:2]
            position = 0
            while position < length:
                self.check()
                blob = os.pread(self.parent.part_stream.fileno(), min(image.CHUNK, length - position), offset + position)
                require(blob, "分割副本讀取截斷")
                out.write(blob)
                checksum.update(blob)
                position += len(blob)
            out.flush()
            completed = image.identity(out)
        self.part_stream = self.stack.enter_context(image.safe.open_file(self.output_fd, self.partition.name))
        require(image.identity(self.part_stream) == completed, "暫存分割在寫入完成後遭置換或變更")
        self.part_identity = image.identity(self.part_stream)
        code, data, error = image.bounded_run(
            ["/usr/sbin/blkid", "-p", "-o", "export", f"/proc/self/fd/{self.part_stream.fileno()}"],
            timeout=self.check(), stdout_limit=65536, pass_fds=(self.part_stream.fileno(),))
        require(code == 0 and not error.strip(), "分割檔案系統身分無法核對")
        values = {}
        for line in data.decode("ascii").splitlines():
            key, separator, value = line.partition("=")
            require(separator and key not in values, "blkid 身分欄位無效或重複")
            values[key] = value
        require(values.get("TYPE") in ("ext2", "ext3", "ext4", "vfat")
                and values["TYPE"].startswith(self.filesystem), "分割檔案系統類型不符")
        self.filesystem_uuid = values.get("UUID", "")
        require(re.fullmatch(r"[0-9A-Fa-f-]{9,36}", self.filesystem_uuid), "分割缺少 UUID")
        self.report.update(source_verified=True, partition={**self.metadata, "sha256": checksum.hexdigest(),
                                                           "bytes": length},
                           filesystem_uuid=self.filesystem_uuid, filesystem=values["TYPE"])

    def read_file(self, path):
        if self.filesystem != "vfat":
            return super().read_file(path)
        image.image_path(path)
        self.check()
        require(self.ready and image.identity(self.part_stream) == self.part_identity, "分割未就緒或已變動")
        code, blob, diagnostic = image.bounded_run(
            ["/usr/bin/mtype", "-i", f"/proc/self/fd/{self.part_stream.fileno()}", "::" + path],
            timeout=min(90, self.check()), stdout_limit=image.MAX_FILE,
            pass_fds=(self.part_stream.fileno(),))
        require(image.identity(self.part_stream) == self.part_identity, "FAT 在讀取期間變動")
        number = len(self.records)
        name = f"query-{number:04d}.stderr"
        self._save(name, diagnostic)
        self.records.append({"command": "mtype " + path, "returncode": code,
                             "stdout": image.digest(blob), "stderr": image.digest(diagnostic), "stderr_file": name})
        if code == 1 and not blob and re.fullmatch(rb'(?:/usr/bin/)?mtype: File "[^"\r\n]+" not found\s*', diagnostic):
            raise FileNotFoundError(path)
        require(code == 0 and not diagnostic.strip(), "FAT 唯讀查詢失敗")
        if path not in self.cache:
            name = f"file-{len(self.cache):04d}.bin"
            self._save(name, blob)
            self.cache[path] = {"file": name, "resolved": path, "links": [], "digest": image.digest(blob)}
        require(self.cache[path]["digest"] == image.digest(blob), "FAT 重複讀取內容改變")
        return blob

    def _query(self, operation, path, limit=65536, optional=False):
        if (self is getattr(self.parent, "root_volume", None)
                and getattr(self.parent, "boot_volume", self) is not self):
            require(not path.startswith("/boot/"), "跨開機掛載的符號連結須另行解析")
        return super()._query(operation, path, limit, optional)

    def _symlink_target(self, path, target):
        if self is getattr(self.parent, "boot_volume", None) and self is not self.parent.root_volume:
            require(not target.startswith("/"), "獨立開機區的絕對連結須以完整掛載視圖解析")


class DiskReader(image.ImageReader):
    """來源完整核對後以標準工具解析；不掛載、不修復分割、不執行映像腳本。"""

    def _extract(self):
        self.volumes = []
        source_root = self.stack.enter_context(image.safe.open_root(self.source.parent))
        source = self.stack.enter_context(image.safe.open_file(source_root, self.source.name))
        before = image.identity(source)
        require(0 < before[2] <= self.maximum, "來源大小超界")
        checksum = hashlib.sha256()
        count = 0
        while chunk := source.read(image.CHUNK):
            self.check()
            count += len(chunk)
            require(count <= min(self.maximum, before[2]), "來源在摘要核對期間增長或超界")
            checksum.update(chunk)
        require(count == before[2] and image.identity(source) == before and checksum.hexdigest() == self.expected,
                "完整來源 SHA-256 不符或來源改變")
        source.seek(0)
        compressed = source.read(6) == b"\xfd7zXZ\0"
        source.seek(0)
        self.partition = self.output / "partition.tmp"
        total, raw_hash = 0, hashlib.sha256()
        with image.safe.open_file(self.output_fd, self.partition.name, create=True) as stream:
            self.partition_created = True
            self.created_identity = image.identity(stream)[:2]
            for blob in self._xz(source) if compressed else self._raw(source):
                self.check()
                require(total + len(blob) <= self.maximum, "原映像解壓超界")
                space = os.fstatvfs(self.output_fd)
                require(space.f_bavail * space.f_frsize >= len(blob) + 64 * image.CHUNK, "映像暫存空間不足")
                stream.write(blob)
                total += len(blob)
                raw_hash.update(blob)
            stream.flush()
            completed = image.identity(stream)
        require(total >= 1024 and total % 512 == 0 and image.identity(source) == before,
                "原映像磁區長度不符或來源改變")
        self.part_stream = self.stack.enter_context(image.safe.open_file(self.output_fd, self.partition.name))
        require(image.identity(self.part_stream) == completed, "暫存原映像在寫入完成後遭置換或變更")
        self.part_identity = image.identity(self.part_stream)
        fd = self.part_stream.fileno()
        raw_path = f"/proc/self/fd/{fd}"
        code, blob, error = image.bounded_run(["/usr/sbin/sfdisk", "--json", raw_path],
            timeout=self.check(), stdout_limit=1024**2, pass_fds=(fd,))
        self._save("partition-table.json", blob)
        self._save("partition-table.stderr", error)
        require(code == 0 and not error.strip(), "分割表解析失敗或需修復，禁止自動修復")
        rows = partitions(image.safe.parse_manifest(blob), raw_path, total)
        if rows[0]["table"] == "gpt":
            protective_mbr(os.pread(fd, 512, 0), total)
        command = ["/usr/sbin/sgdisk", "--verify", raw_path] if rows[0]["table"] == "gpt" else [
            "/usr/sbin/sfdisk", "--verify", raw_path]
        code, verify, error = image.bounded_run(command, timeout=self.check(), stdout_limit=65536, pass_fds=(fd,))
        self._save("partition-verify.stdout", verify)
        self._save("partition-verify.stderr", error)
        marker = b"No problems found." if rows[0]["table"] == "gpt" else b"No errors detected."
        require(code == 0 and not error.strip() and marker in verify, "分割表一致性核對未通過")
        volumes = self.volumes
        for part in rows:
            self.check()
            header = os.pread(fd, 2048, part["start_lba"] * 512)
            filesystem = "ext" if header[1080:1082] == b"\x53\xef" else "vfat" if (
                header[54:62] in (b"FAT12   ", b"FAT16   ") or header[82:90] == b"FAT32   ") else None
            if filesystem is None:
                continue
            space = os.fstatvfs(self.output_fd)
            require(space.f_bavail * space.f_frsize >= part["sectors"] * 512 + 64 * image.CHUNK,
                    "分割暫存空間不足")
            volumes.append(PartitionView(self, part, filesystem).__enter__())
        require(volumes and len({v.filesystem_uuid.lower() for v in volumes}) == len(volumes),
                "檔案系統缺失或 UUID 重複")
        roots = []
        for volume in volumes:
            if volume.filesystem != "ext":
                continue
            try:
                release = volume.read_file("/etc/armbian-release")
            except FileNotFoundError:
                continue
            require(release and len(release) <= 65536, "根系統版本檔空白或超界")
            roots.append(volume)
        require(len(roots) == 1, "必須唯一找到含 armbian-release 的根系統")
        self.root_volume = roots[0]
        mounts = fstab_mounts(self.root_volume.read_file("/etc/fstab"))
        require(matches(self.root_volume, mounts["/"]["source"]), "fstab 根媒體與實際 UUID 不符")
        self.boot_volume = self.root_volume
        if "/boot" in mounts:
            choices = [volume for volume in volumes if matches(volume, mounts["/boot"]["source"])]
            require(len(choices) == 1 and choices[0] is not self.root_volume,
                    "無法唯一定位獨立開機分割")
            self.boot_volume = choices[0]
            require(mounts["/boot"]["filesystem"].startswith(self.boot_volume.filesystem),
                    "fstab 開機檔案系統與分割不符")
        self.filesystem_uuid = self.root_volume.filesystem_uuid
        self.report.update(source_verified=True, source_kind="xz" if compressed else "raw",
            source_digest={"bytes": before[2], "sha256": self.expected},
            raw={"bytes": total, "sha256": raw_hash.hexdigest()},
            partition=self.root_volume.report["partition"], partitions=rows,
            boot_partition=self.boot_volume.report["partition"], mounts=mounts,
            filesystem_uuid=self.filesystem_uuid, layout_reader="multi-partition-v1")
        require(image.identity(self.part_stream) == self.part_identity, "暫存原映像在解析期間改變")

    def read_file(self, path):
        image.image_path(path)
        self.check()
        require(self.ready, "映像尚未完成核對")
        volume, inside = self.root_volume, path
        if path.startswith("/boot/") and self.boot_volume is not self.root_volume:
            volume, inside = self.boot_volume, path[5:]
        try:
            blob = volume.read_file(inside)
        except FileNotFoundError:
            query = volume.records[-1]
            metadata, diagnostic = image.safe.fingerprint(volume.output_fd, query["stderr_file"], limit=65536, keep=True)
            require(metadata == query["stderr"], "缺檔查詢證據已變動")
            name = f"query-{len(self.records):04d}.stderr"
            self._save(name, diagnostic)
            self.records.append({**query, "stderr_file": name, "lookup_path": path,
                                 "volume_index": volume.metadata["index"]})
            raise
        record = volume.cache[inside]
        if volume is not self.root_volume:
            require(not any(link["target"].startswith("/") for link in record["links"]),
                    "獨立開機區的絕對連結須以完整掛載視圖解析，禁止誤取分割內同名檔案")
        # 不把根分割內跨 /boot 的連結誤當成未掛載的根分割檔案。
        if volume is self.root_volume and self.boot_volume is not volume:
            require(not record["resolved"].startswith("/boot/"), "跨開機掛載的符號連結須另行解析")
        if path not in self.cache:
            name = f"file-{len(self.cache):04d}.bin"
            self._save(name, blob)
            self.cache[path] = {**record, "file": name, "volume_index": volume.metadata["index"]}
        require(self.cache[path]["digest"] == image.digest(blob), "重複讀取組件內容變動")
        return blob

    def __exit__(self, exc_type, exc, traceback):
        failure = None
        for volume in reversed(getattr(self, "volumes", [])):
            try:
                volume.__exit__(exc_type, exc, traceback)
            except (ValueError, OSError) as error:
                failure = failure or error
        self.volumes = []
        super().__exit__(type(failure) if failure else exc_type, failure or exc, traceback)
        if failure is not None and exc_type is None:
            raise failure
