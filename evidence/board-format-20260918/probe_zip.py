#!/usr/bin/env python3
"""只用 HTTP Range 取得 ZIP 目錄及小型刷機控制檔，記錄可重查證據。"""

import argparse
import hashlib
import io
import json
from pathlib import Path
import re
import urllib.request
import zipfile


class RemoteZip(io.RawIOBase):
    def __init__(self, url, size):
        self.url = url
        self.size = size
        self.pos = 0
        self.requests = []

    def seekable(self):
        return True

    def readable(self):
        return True

    def seek(self, offset, whence=0):
        self.pos = offset if whence == 0 else self.pos + offset if whence == 1 else self.size + offset
        if self.pos < 0 or self.pos > self.size:
            raise ValueError("ZIP 偏移超出檔案")
        return self.pos

    def tell(self):
        return self.pos

    def read(self, size=-1):
        if size < 0:
            size = self.size - self.pos
        size = min(size, self.size - self.pos)
        if size == 0:
            return b""
        if size > 1024 * 1024:
            raise ValueError("拒絕讀取超過 1 MiB 的 ZIP 區段")
        start, end = self.pos, self.pos + size - 1
        request = urllib.request.Request(self.url, headers={"User-Agent": "Mozilla/5.0", "Range": f"bytes={start}-{end}", "Accept-Encoding": "identity"})
        with urllib.request.urlopen(request, timeout=40) as response:
            content_range = response.headers.get("Content-Range", "")
            if response.status != 206 or content_range != f"bytes {start}-{end}/{self.size}":
                raise ValueError(f"伺服器未遵守 Range：{response.status} {content_range}")
            payload = response.read(size + 1)
            if len(payload) != size:
                raise ValueError("Range 長度不符")
            self.requests.append({"start": start, "end": end, "status": response.status,
                                  "content_range": content_range, "bytes": len(payload),
                                  "sha256": hashlib.sha256(payload).hexdigest(),
                                  "etag": response.headers.get("ETag"),
                                  "last_modified": response.headers.get("Last-Modified")})
        self.pos += size
        return payload


def main():
    parser = argparse.ArgumentParser(description="擷取遠端 ZIP 控制檔及目錄證據")
    parser.add_argument("--url", required=True)
    parser.add_argument("--size", required=True, type=int)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    remote = RemoteZip(args.url, args.size)
    entries, selected = [], []
    with zipfile.ZipFile(remote) as archive:
        for info in archive.infolist():
            entries.append({"path": info.filename, "size": info.file_size, "compressed_size": info.compress_size,
                            "compress_type": info.compress_type, "crc32": f"{info.CRC:08x}",
                            "header_offset": info.header_offset, "zip_version": info.extract_version,
                            "flag_bits": info.flag_bits})
            if re.fullmatch(r"(?:[^/]+/)?(?:fastboot\.ya?ml|partition[^/]*\.json)", info.filename):
                if info.file_size > 100000:
                    raise ValueError("控制檔異常過大")
                data = archive.read(info)
                output = args.out / info.filename
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_bytes(data)
                selected.append({"path": info.filename, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(), "crc32": f"{info.CRC:08x}"})
                print(info.filename, len(data), hashlib.sha256(data).hexdigest(), flush=True)
    report = {"url": args.url, "archive_bytes": args.size, "archive_sha256": None,
              "scope": "僅擷取 ZIP 中央目錄與小型控制檔；未下載或驗證完整映像雜湊。zipfile 已驗解壓後 CRC32。",
              "entries": entries, "files": selected, "requests": remote.requests,
              "transferred_bytes": sum(item["bytes"] for item in remote.requests)}
    (args.out / "retrieval.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"entries": len(entries), "files": len(selected), "transferred_bytes": report["transferred_bytes"]}))


if __name__ == "__main__":
    main()
