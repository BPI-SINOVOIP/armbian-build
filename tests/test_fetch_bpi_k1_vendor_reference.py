"""以小型合成 ZIP 驗證固定元件取得工具，不存取網路或實體媒體。"""

import hashlib
import io
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import importlib.util

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("fetch_bpi_k1_vendor_reference", ROOT / "tools/fetch_bpi_k1_vendor_reference.py")
tool = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(tool)


class FetchTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.archive = self.root / "官方.zip"
        self.lock_path = self.root / "來源鎖.json"
        self.output = self.root / "新參考"
        self.contents = {name: (name.encode() + b"\n") * 9 for name in sorted(tool.MEMBERS)}
        with zipfile.ZipFile(self.archive, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for index, (name, data) in enumerate(self.contents.items()):
                archive.writestr(name, data, compress_type=zipfile.ZIP_STORED if index == 0 else zipfile.ZIP_DEFLATED)
        with zipfile.ZipFile(self.archive) as archive:
            records = [{"path": item.filename, "size": item.file_size,
                        "compressed_size": item.compress_size, "header_offset": item.header_offset,
                        "sha256": hashlib.sha256(self.contents[item.filename]).hexdigest(),
                        "crc32": f"{item.CRC:08x}"} for item in archive.infolist()]
        self.lock = {"url": tool.SOURCE_URL, "archive_bytes": self.archive.stat().st_size, "files": records}
        self.save_lock()

    def save_lock(self):
        self.lock_path.write_text(json.dumps(self.lock))

    def rejected(self):
        with self.assertRaises(ValueError):
            tool.fetch(self.lock_path, self.output, self.archive)
        self.assertFalse(self.output.exists(), "失敗後不應留下半成品")

    def test_valid_zip_and_retrieval(self):
        result = tool.fetch(self.lock_path, self.output, self.archive)
        self.assertEqual(result["files"], self.lock["files"])
        self.assertIsNone(result["archive_full_sha256"])
        for name, content in self.contents.items():
            self.assertEqual((self.output / name).read_bytes(), content)
        self.assertEqual(json.loads((self.output / "retrieval.json").read_text()), result)

    def test_wrong_sha(self):
        self.lock["files"][0]["sha256"] = "0" * 64
        self.save_lock()
        self.rejected()

    def test_wrong_crc(self):
        self.lock["files"][0]["crc32"] = "00000000"
        self.save_lock()
        self.rejected()

    def test_wrong_zip_name(self):
        data = bytearray(self.archive.read_bytes())
        data[30] ^= 1
        self.archive.write_bytes(data)
        self.rejected()

    def test_wrong_header_offset(self):
        self.lock["files"][0]["header_offset"] += 1
        self.save_lock()
        self.rejected()

    def test_existing_output_preserved(self):
        self.output.mkdir()
        marker = self.output / "既有檔案"
        marker.write_bytes(b"saved")
        with self.assertRaises(ValueError):
            tool.fetch(self.lock_path, self.output, self.archive)
        self.assertEqual(marker.read_bytes(), b"saved")

    def test_wrong_archive_size(self):
        self.archive.write_bytes(self.archive.read_bytes() + b"extra")
        self.rejected()

    def test_encryption_rejected(self):
        data = bytearray(self.archive.read_bytes())
        struct.pack_into("<H", data, 6, 1)
        self.archive.write_bytes(data)
        self.rejected()

    def test_deflate_limit(self):
        item = self.lock["files"][1]
        item["size"] = 1
        data = bytearray(self.archive.read_bytes())
        struct.pack_into("<I", data, item["header_offset"] + 22, 1)
        self.archive.write_bytes(data)
        self.save_lock()
        self.rejected()

    def response(self, status=206, content_range="bytes 5-7/100", body=b"abc"):
        class Reply(io.BytesIO):
            def geturl(self):
                return tool.SOURCE_URL
        reply = Reply(body)
        reply.status = status
        reply.headers = {"Content-Range": content_range, "Content-Length": "3"}
        return reply

    def test_http_206_exact_range(self):
        with patch.object(tool.urllib.request, "urlopen", return_value=self.response()) as opened:
            self.assertEqual(tool.RangeReader(tool.SOURCE_URL, 100).read(5, 3), b"abc")
        self.assertEqual(opened.call_args.args[0].get_header("Range"), "bytes=5-7")

    def test_http_200_rejected_before_body(self):
        reply = self.response(status=200)
        with patch.object(reply, "read", side_effect=AssertionError("不應讀取整包")):
            with patch.object(tool.urllib.request, "urlopen", return_value=reply):
                with self.assertRaisesRegex(ValueError, "206"):
                    tool.RangeReader(tool.SOURCE_URL, 100).read(5, 3)

    def test_http_wrong_range_total_or_length(self):
        for content_range, body in (("bytes 5-7/101", b"abc"), ("bytes 4-6/100", b"abc"),
                                    ("bytes 5-7/100", b"ab"), ("bytes 5-7/100", b"abcd")):
            with self.subTest(content_range=content_range, length=len(body)):
                with patch.object(tool.urllib.request, "urlopen", return_value=self.response(content_range=content_range, body=body)):
                    with self.assertRaises(ValueError):
                        tool.RangeReader(tool.SOURCE_URL, 100).read(5, 3)


if __name__ == "__main__":
    unittest.main()
