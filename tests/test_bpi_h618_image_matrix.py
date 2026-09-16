#!/usr/bin/env python3
"""唯讀矩陣離線回歸；只使用臨時小型映像，不接觸實板或客戶原檔。"""

from contextlib import redirect_stdout, redirect_stderr
import copy
import csv
import hashlib
import importlib.util
import io
import json
import lzma
import os
from pathlib import Path
import stat
import struct
import sys
import tempfile
import unittest
from unittest import mock


TOOLS = Path(__file__).resolve().parents[1] / "tools"
SPEC = importlib.util.spec_from_file_location("image_matrix", TOOLS / "bpi_h618_image_matrix.py")
matrix = importlib.util.module_from_spec(SPEC)
with mock.patch.object(sys, "path", [str(TOOLS), *sys.path]):
    SPEC.loader.exec_module(matrix)

PROFILE = "bananapim4zero"
VARIANTS = ("minimal", "xfce_desktop")
CAPACITY = 31289507840
COMMIT = "a" * 40


def raw_image(sectors=16):
    data = bytearray(sectors * 512)
    struct.pack_into("<B3sB3sII", data, 446, 0, bytes(3), 0x83, bytes(3), 1, sectors - 1)
    data[510:512] = b"\x55\xaa"
    data[512:] = (bytes(range(256)) * (len(data) // 256))[:len(data) - 512]
    return bytes(data)


def filename(release="bookworm", variant="minimal", profile=PROFILE, suffix=""):
    return f"Armbian-unofficial_26.11.0-trunk_{profile.capitalize()}_{release}_current_6.18.49_{variant}{suffix}.img.xz"


class MatrixTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "source"
        self.root.mkdir()
        self.evidence_parent = self.base / "evidence"
        self.evidence_parent.mkdir()
        self.raw = raw_image()
        for release in matrix.RELEASES:
            for variant in VARIANTS:
                self.put(filename(release, variant), lzma.compress(self.raw))

    def put(self, name, compressed):
        (self.root / name).write_bytes(compressed)
        (self.root / (name + ".sha")).write_text(hashlib.sha256(compressed).hexdigest() + "  " + name + "\n")

    def scan(self, **kwargs):
        return matrix.inventory(self.root, PROFILE, VARIANTS, **kwargs)

    def single(self, compressed=None, raw=None, capacity=CAPACITY, maximum=matrix.MAX_RAW):
        if raw is not None:
            compressed = lzma.compress(raw)
        if compressed is not None:
            self.put(filename(), compressed)
        entry = self.scan()["entries"][0]
        with matrix.safe.open_root(self.root) as directory:
            return matrix.stream_verify(directory, entry, capacity, maximum)

    def prepared(self):
        made = matrix.write_inventory(self.scan(), self.evidence_parent)
        self.evidence = Path(made["evidence"])
        self.inventory_sha = made["inventory_sha256"]
        return made

    def verify(self, capacity=CAPACITY, maximum=matrix.MAX_RAW):
        with redirect_stderr(io.StringIO()):
            return matrix.verify(self.evidence, self.inventory_sha, capacity, max_raw_bytes=maximum)

    def test_inventory_has_ten_exact_roles_and_no_guessed_source(self):
        with mock.patch.object(matrix, "stream_verify", side_effect=AssertionError("不得解壓")):
            report = self.scan()
        self.assertTrue(report["ok"], report["issues"])
        self.assertEqual(len(report["entries"]), 10)
        self.assertFalse(report["large_files_read"])
        self.assertEqual({entry["variant"] for entry in report["entries"]}, set(VARIANTS))
        self.assertTrue(all(entry["source_commit"] is None for entry in report["entries"]))
        for field in matrix.LIMITS:
            if field != "scope":
                self.assertIs(report[field], False)

    def test_inventory_does_not_read_image_payload(self):
        original = matrix.safe.open_file

        class NoRead:
            def __init__(self, stream):
                self.stream = stream

            def fileno(self):
                return self.stream.fileno()

            def read(self, *args):
                raise AssertionError("盤點不得讀取映像內容")

        from contextlib import contextmanager

        @contextmanager
        def guarded(directory, name, **kwargs):
            with original(directory, name, **kwargs) as stream:
                yield NoRead(stream) if name.endswith(".img.xz") else stream

        with mock.patch.object(matrix.safe, "open_file", guarded):
            self.assertTrue(self.scan()["ok"])

    def test_missing_duplicate_and_unexpected_combinations_are_reported(self):
        (self.root / filename()).unlink()
        self.put(filename("jammy", suffix="_batch2"), lzma.compress(self.raw))
        self.put(filename("unknown"), lzma.compress(self.raw))
        report = self.scan()
        issue = next(item for item in report["issues"] if "duplicates" in item)
        self.assertFalse(report["ok"])
        self.assertIn(("bookworm", "minimal"), issue["missing"])
        self.assertIn(("unknown", "minimal"), issue["unexpected"])
        self.assertEqual(issue["duplicates"][0]["release"], "jammy")

    def test_berry_and_emac_never_match_plain_zero(self):
        for profile in ("bananapim4berry", "bananapim4zeroemac"):
            name = filename(profile=profile)
            self.put(name, lzma.compress(self.raw))
            self.assertFalse(self.scan()["ok"])
            (self.root / name).unlink()
        with self.assertRaises(matrix.MatrixError):
            matrix.inventory(self.root, "bananapim4berry", VARIANTS)

    def test_cli_spelling_is_preserved_and_not_inferred(self):
        for role in ("cli", "CLI", "minimal", "xfce_desktop"):
            self.assertEqual(matrix.parse_name(filename(variant=role))["variant"], role)
        for name in (filename().replace("_minimal", ""), filename(variant="server"), "../" + filename()):
            with self.subTest(name=name), self.assertRaises(matrix.MatrixError):
                matrix.parse_name(name)
        with self.assertRaises(matrix.MatrixError):
            matrix.inventory(self.root, PROFILE, ["minimal", "minimal"])

    def test_source_requires_record_and_keeps_unknown_by_default(self):
        with self.assertRaises(matrix.MatrixError):
            matrix.metadata(None, [], COMMIT, None)
        record = self.root / "Release-Notes-zh-TW.md"
        record.write_text("來源提交：`" + COMMIT + "`\n")
        report = self.scan(source_commit=COMMIT, source_record=record.name)
        self.assertTrue(report["ok"])
        self.assertTrue(all(item["source_commit"] == COMMIT for item in report["entries"]))
        report = self.scan(source_commit="b" * 40, source_record=record.name)
        self.assertFalse(report["ok"])

    def write_tsv(self, name, rows):
        with (self.root / name).open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]), delimiter="\t")
            writer.writeheader()
            writer.writerows(rows)

    def provenance(self):
        return [{"release": entry["release"], "profile": "cli" if entry["variant"] == "minimal" else "xfce",
                 "artifact_source_commit": COMMIT, "xz_filename": entry["path"], "userpatches_sha256": "unrecorded"}
                for entry in self.scan()["entries"]]

    def test_structured_provenance_preserves_cli_and_unrecorded(self):
        self.write_tsv("BUILD_PROVENANCE.tsv", self.provenance())
        report = self.scan()
        self.assertTrue(report["ok"], report["issues"])
        entry = report["entries"][0]
        self.assertEqual(entry["variant"], "minimal")
        self.assertEqual(entry["source_commit"], COMMIT)
        self.assertEqual(entry["declared_metadata"]["BUILD_PROVENANCE.tsv"]["profile"], "cli")
        self.assertEqual(entry["declared_metadata"]["BUILD_PROVENANCE.tsv"]["userpatches_sha256"], "unrecorded")

    def test_provenance_duplicate_missing_commit_and_release_mismatch(self):
        rows = self.provenance()
        for changed in (rows + [rows[0]], rows[:-1],
                        [{**rows[0], "artifact_source_commit": "unknown"}, *rows[1:]],
                        [{**rows[0], "release": "wrong"}, *rows[1:]]):
            self.write_tsv("BUILD_PROVENANCE.tsv", changed)
            self.assertFalse(self.scan()["ok"])

    def test_image_manifest_raw_size_and_hash_are_verified(self):
        rows = [{"release": entry["release"], "profile": "cli" if entry["variant"] == "minimal" else "xfce",
                 "raw_size": str(len(self.raw)), "raw_sha256": hashlib.sha256(self.raw).hexdigest(),
                 "xz_size": str(entry["identity"]["st_size"]),
                 "xz_sha256": entry["expected_compressed_sha256"], "xz_filename": entry["path"]}
                for entry in self.scan()["entries"]]
        self.write_tsv("IMAGE_MANIFEST.tsv", rows)
        self.assertTrue(self.scan()["ok"])
        self.assertTrue(self.single()["ok"])
        rows[0]["raw_sha256"] = "0" * 64
        self.write_tsv("IMAGE_MANIFEST.tsv", rows)
        with self.assertRaisesRegex(matrix.MatrixError, "發布清單"):
            self.single()
        rows[0]["xz_size"] = "1"
        self.write_tsv("IMAGE_MANIFEST.tsv", rows)
        self.assertFalse(self.scan()["ok"])

    def test_conflicting_source_records_are_rejected(self):
        self.write_tsv("BUILD_PROVENANCE.tsv", self.provenance())
        (self.root / "Release-Notes-zh-TW.md").write_text("來源提交：" + "b" * 40)
        report = self.scan(source_commit="b" * 40, source_record="Release-Notes-zh-TW.md")
        self.assertFalse(report["ok"])
        self.assertIn("衝突", report["issues"][0]["error"])

    def test_checksum_missing_wrong_name_or_malformed(self):
        sidecar = self.root / (filename() + ".sha")
        original = sidecar.read_bytes()
        for blob in (b"", original.replace(filename().encode(), b"other.img.xz"), b"0" * 63 + b"  x\n"):
            sidecar.write_bytes(blob)
            self.assertFalse(self.scan()["ok"])
        sidecar.unlink()
        self.assertFalse(self.scan()["ok"])

    def test_stream_checks_both_hashes_size_and_mbr(self):
        result = self.single()
        self.assertTrue(result["ok"])
        self.assertEqual(result["raw"], {"bytes": len(self.raw), "sha256": hashlib.sha256(self.raw).hexdigest()})
        self.assertEqual(result["compressed"]["sha256"], hashlib.sha256((self.root / filename()).read_bytes()).hexdigest())
        self.assertEqual(result["mbr"]["partitions"][0]["start_lba"], 1)
        self.assertEqual(result["mbr"]["capacity_bytes"], CAPACITY)

    def test_concatenated_xz_and_padding_across_small_chunks(self):
        compressed = lzma.compress(self.raw[:512]) + bytes(8) + lzma.compress(self.raw[512:]) + bytes(4)
        with mock.patch.object(matrix, "CHUNK", 7):
            result = self.single(compressed)
        self.assertEqual(result["xz_streams"], 2)
        self.assertEqual(result["raw"]["sha256"], hashlib.sha256(self.raw).hexdigest())

    def test_large_expansion_is_streamed_with_bounded_output(self):
        raw = raw_image(8192)
        original = lzma.LZMADecompressor
        sizes = []

        class Decoder:
            def __init__(self, **kwargs):
                self.decoder = original(**kwargs)

            def decompress(self, data, max_length):
                self.assert_bound(max_length)
                result = self.decoder.decompress(data, max_length=max_length)
                sizes.append(len(result))
                return result

            @staticmethod
            def assert_bound(max_length):
                if max_length != matrix.CHUNK:
                    raise AssertionError("解壓輸出必須有固定上限")

            def __getattr__(self, name):
                return getattr(self.decoder, name)

        with mock.patch.object(matrix.lzma, "LZMADecompressor", Decoder):
            result = self.single(raw=raw)
        self.assertEqual(result["raw"]["bytes"], len(raw))
        self.assertLessEqual(max(sizes), matrix.CHUNK)
        self.assertGreater(len(sizes), 1)

    def test_truncated_corrupt_trailing_and_bad_padding_rejected(self):
        good = lzma.compress(self.raw)
        corrupt = bytearray(good)
        corrupt[len(corrupt) // 2] ^= 1
        for compressed in (good[:-1], good[:20], bytes(corrupt), good + b"junk", good + b"\0",
                           good + b"\0" + good, b"\0\0\0\0" + good, lzma.compress(self.raw, format=lzma.FORMAT_ALONE)):
            with self.subTest(length=len(compressed)), self.assertRaises(matrix.MatrixError):
                self.single(compressed)

    def test_xz_without_integrity_check_and_memory_limit_rejected(self):
        with self.assertRaisesRegex(matrix.MatrixError, "檢查碼"):
            self.single(lzma.compress(self.raw, check=lzma.CHECK_NONE))
        with mock.patch.object(matrix, "MEMLIMIT", 1024), self.assertRaisesRegex(matrix.MatrixError, "記憶體"):
            self.single()

    def test_raw_limit_and_capacity_are_separate(self):
        with self.assertRaisesRegex(matrix.MatrixError, "安全上限"):
            self.single(maximum=len(self.raw) - 1)
        with self.assertRaisesRegex(matrix.MatrixError, "容量"):
            self.single(capacity=len(self.raw) - 1)
        self.assertTrue(self.single(capacity=len(self.raw))["ok"])

    def test_mbr_signature_alignment_bounds_overlap_and_unsupported_types(self):
        cases = []
        bad = bytearray(self.raw)
        bad[510] = 0
        cases.append(bytes(bad))
        cases.append(self.raw[:-1])
        for kind, start, sectors in ((0x83, 1, 16), (0x83, 0, 1), (0xEE, 1, 15), (0x05, 1, 15), (0, 1, 15)):
            bad = bytearray(self.raw)
            struct.pack_into("<B3sB3sII", bad, 446, 0, bytes(3), kind, bytes(3), start, sectors)
            cases.append(bytes(bad))
        bad = bytearray(self.raw)
        struct.pack_into("<B3sB3sII", bad, 462, 0, bytes(3), 0x83, bytes(3), 2, 2)
        cases.append(bytes(bad))
        bad = bytearray(self.raw)
        bad[446] = 1
        cases.append(bytes(bad))
        bad = bytearray(self.raw)
        bad[446:510] = bytes(64)
        cases.append(bytes(bad))
        for raw in cases:
            with self.subTest(header=raw[446:462]), self.assertRaises(matrix.MatrixError):
                self.single(raw=raw)

    def test_wrong_expected_compressed_or_raw_hash_rejected(self):
        entry = self.scan()["entries"][0]
        for changed in ({**entry, "expected_compressed_sha256": "0" * 64},
                        {**entry, "expected_raw": {"bytes": len(self.raw), "sha256": "0" * 64}}):
            with matrix.safe.open_root(self.root) as directory, self.assertRaises(matrix.MatrixError):
                matrix.stream_verify(directory, changed, CAPACITY)

    def test_source_symlink_fifo_and_directory_rejected(self):
        target = self.root / filename()
        target.unlink()
        target.symlink_to(self.root / filename("jammy"))
        self.assertFalse(self.scan()["ok"])
        target.unlink()
        os.mkfifo(target)
        self.assertFalse(self.scan()["ok"])
        target.unlink()
        target.mkdir()
        self.assertFalse(self.scan()["ok"])

    def test_device_rejected_before_io_open(self):
        with matrix.safe.open_root(self.root) as directory:
            with mock.patch.object(matrix.safe.os, "open", wraps=os.open) as opening:
                with mock.patch.object(matrix.safe.os, "fstat", return_value=mock.Mock(st_mode=stat.S_IFBLK)):
                    with self.assertRaises(matrix.MatrixError):
                        matrix.file_identity(directory, filename())
                self.assertEqual(opening.call_count, 1)
                self.assertTrue(opening.call_args.args[1] & os.O_PATH)

    def test_source_ancestor_and_sidecar_symlink_rejected(self):
        link = self.base / "link"
        link.symlink_to(self.base, target_is_directory=True)
        with self.assertRaises(OSError):
            matrix.inventory(link / "source", PROFILE, VARIANTS)
        sidecar = self.root / (filename() + ".sha")
        sidecar.unlink()
        sidecar.symlink_to(self.root / (filename("jammy") + ".sha"))
        self.assertFalse(self.scan()["ok"])

    def test_evidence_must_be_outside_source_and_new(self):
        report = self.scan()
        before = sorted(self.root.iterdir())
        with self.assertRaises(matrix.MatrixError):
            matrix.write_inventory(report, self.root)
        self.assertEqual(before, sorted(self.root.iterdir()))
        made = matrix.write_inventory(report, self.evidence_parent)
        again = matrix.write_inventory(report, self.evidence_parent)
        self.assertNotEqual(made["evidence"], again["evidence"])
        with matrix.safe.open_root(made["evidence"]) as directory:
            with self.assertRaises(FileExistsError):
                matrix.save_json(directory, "inventory.json", report)

    def test_evidence_parent_symlink_rejected(self):
        link = self.base / "link"
        link.symlink_to(self.evidence_parent, target_is_directory=True)
        with self.assertRaises(OSError):
            matrix.write_inventory(self.scan(), link)

    def test_verify_and_resume_without_rechecking_success(self):
        self.prepared()
        first = self.verify()
        self.assertTrue(first["ok"])
        with mock.patch.object(matrix, "stream_verify", side_effect=AssertionError("成功檔不應重驗")):
            second = self.verify()
        self.assertTrue(second["ok"])
        self.assertTrue(all(item["resumed"] for item in second["results"]))
        self.assertNotEqual(first["run"], second["run"])

    def test_interruption_preserves_success_and_only_retries_remaining(self):
        self.prepared()
        original = matrix.stream_verify
        count = 0

        def interrupted(*args):
            nonlocal count
            count += 1
            if count == 2:
                raise KeyboardInterrupt
            return original(*args)

        with mock.patch.object(matrix, "stream_verify", interrupted), self.assertRaises(KeyboardInterrupt):
            self.verify()
        with mock.patch.object(matrix, "stream_verify", wraps=original) as checking:
            report = self.verify()
        self.assertTrue(report["ok"])
        self.assertEqual(checking.call_count, 9)
        self.assertTrue(report["results"][0]["resumed"])

    def test_failure_is_not_reused(self):
        self.prepared()
        original = matrix.stream_verify
        count = 0

        def fails_once(*args):
            nonlocal count
            count += 1
            if count == 1:
                raise matrix.MatrixError("測試失敗")
            return original(*args)

        with mock.patch.object(matrix, "stream_verify", fails_once):
            self.assertFalse(self.verify()["ok"])
        with mock.patch.object(matrix, "stream_verify", wraps=original) as checking:
            self.assertTrue(self.verify()["ok"])
        self.assertEqual(checking.call_count, 1)

    def test_corrupt_or_incomplete_receipt_is_not_reused(self):
        self.prepared()
        first = self.verify()
        run = self.evidence / first["run"]
        (run / "item-00.json").write_text('{"payload":')
        (run / "item-01.json").write_text('{}')
        with mock.patch.object(matrix, "stream_verify", wraps=matrix.stream_verify) as checking:
            report = self.verify()
        self.assertTrue(report["ok"])
        self.assertEqual(checking.call_count, 2)

    def test_receipt_digest_and_semantics_are_checked(self):
        self.prepared()
        first = self.verify()
        for index in range(3):
            path = self.evidence / first["run"] / f"item-{index:02d}.json"
            record = json.loads(path.read_text())
            if index == 0:
                record["sha256"] = "0" * 64
            elif index == 1:
                record["payload"]["result"]["raw"]["bytes"] = CAPACITY + 1
                record["sha256"] = matrix.digest_json(record["payload"])
            else:
                record["payload"]["result"]["mbr"]["partitions"][0]["end_lba_exclusive"] = 100
                record["sha256"] = matrix.digest_json(record["payload"])
            path.write_text(json.dumps(record))
        with mock.patch.object(matrix, "stream_verify", wraps=matrix.stream_verify) as checking:
            report = self.verify()
        self.assertTrue(report["ok"])
        self.assertEqual(checking.call_count, 3)

    def test_receipt_symlink_and_missing_completion_are_not_reused(self):
        self.prepared()
        first = self.verify()
        run = self.evidence / first["run"]
        (run / "item-00.json").unlink()
        (run / "item-00.json").symlink_to(run / "item-02.json")
        path = run / "item-01.json"
        record = json.loads(path.read_text())
        del record["payload"]["completed_utc"]
        record["sha256"] = matrix.digest_json(record["payload"])
        path.write_text(json.dumps(record))
        with mock.patch.object(matrix, "stream_verify", wraps=matrix.stream_verify) as checking:
            self.assertTrue(self.verify()["ok"])
        self.assertEqual(checking.call_count, 2)

    def test_interrupted_evidence_write_is_not_published(self):
        self.prepared()
        with matrix.safe.open_root(self.evidence) as directory:
            with mock.patch.object(matrix.os, "link", side_effect=OSError(5, "測試中斷")):
                with self.assertRaises(OSError):
                    matrix.save_json(directory, "unpublished.json", {"ok": True})
        self.assertFalse((self.evidence / "unpublished.json").exists())
        self.assertTrue(list(self.evidence.glob("partial-*")))

    def test_actual_cli_inventory_and_verify(self):
        out = io.StringIO()
        with redirect_stdout(out):
            code = matrix.main(["inventory", "--root", str(self.root), "--expected-profile", PROFILE,
                                "--variant", "minimal", "--variant", "xfce_desktop",
                                "--evidence-parent", str(self.evidence_parent)])
        self.assertEqual(code, 0)
        made = json.loads(out.getvalue())
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(io.StringIO()):
            code = matrix.main(["verify", "--evidence", made["evidence"],
                                "--inventory-sha256", made["inventory_sha256"], "--capacity-bytes", str(CAPACITY)])
        self.assertEqual(code, 0)
        self.assertEqual(len(json.loads(out.getvalue())["results"]), 10)

    def test_capacity_limits_and_tool_changes_invalidate_receipts(self):
        self.prepared()
        self.verify()
        for capacity, maximum in ((CAPACITY + 512, matrix.MAX_RAW), (CAPACITY, matrix.MAX_RAW - 512)):
            with mock.patch.object(matrix, "stream_verify", wraps=matrix.stream_verify) as checking:
                self.assertTrue(self.verify(capacity, maximum)["ok"])
            self.assertEqual(checking.call_count, 10)
        with mock.patch.object(matrix, "tool_fingerprint", return_value={"changed": "b" * 64}):
            with mock.patch.object(matrix, "stream_verify", wraps=matrix.stream_verify) as checking:
                self.assertTrue(self.verify()["ok"])
            self.assertEqual(checking.call_count, 10)

    def test_changed_original_is_rejected_not_resumed(self):
        self.prepared()
        self.verify()
        path = self.root / filename()
        before = path.stat()
        path.write_bytes(path.read_bytes())
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
        with mock.patch.object(matrix, "stream_verify", side_effect=AssertionError("過期盤點不得重驗")):
            report = self.verify()
        self.assertFalse(report["ok"])
        self.assertIn("身分", report["results"][0]["error"])
        self.assertTrue(all(item["resumed"] for item in report["results"][1:]))

    def test_changed_identity_during_read_rejected(self):
        original = matrix.file_identity
        with mock.patch.object(matrix, "file_identity", wraps=matrix.file_identity) as identities:
            count = 0

            def changed(*args):
                nonlocal count
                count += 1
                value = original(*args)
                if count == 11:
                    value["st_ino"] += 1
                return value

            identities.side_effect = changed
            with self.assertRaisesRegex(matrix.MatrixError, "替換"):
                self.single()

    def test_trusted_inventory_hash_and_matrix_schema_required(self):
        self.prepared()
        with self.assertRaisesRegex(matrix.MatrixError, "SHA-256"):
            matrix.verify(self.evidence, "0" * 64, CAPACITY)
        original = self.scan()
        for change in (lambda d: d.update(schema=True), lambda d: d.update(profile="bananapim4berry"),
                       lambda d: d["entries"].pop(), lambda d: d["entries"][0].update(variant="cli"),
                       lambda d: d["entries"][0].update(expected_compressed_sha256="bad")):
            data = copy.deepcopy(original)
            change(data)
            with self.assertRaises(matrix.MatrixError):
                matrix.validate_inventory(data)

    def test_directory_lock_rejects_parallel_writer(self):
        self.prepared()
        with matrix.locked_evidence(self.evidence), self.assertRaisesRegex(matrix.MatrixError, "另一個"):
            self.verify()

    def test_original_files_and_mtimes_remain_unchanged(self):
        before = {path.name: (path.read_bytes(), path.stat().st_mtime_ns) for path in self.root.iterdir()}
        self.prepared()
        self.verify()
        self.verify()
        after = {path.name: (path.read_bytes(), path.stat().st_mtime_ns) for path in self.root.iterdir()}
        self.assertEqual(before, after)
        self.assertFalse(list(self.evidence.rglob("*.img")))

    def test_cli_is_chinese_and_errors_have_no_hardware_authority(self):
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(matrix.main(["inventory", "--unknown"]), 1)
        report = json.loads(out.getvalue())
        self.assertIn("命令列", report["error"])
        self.assertIs(report["write_authorized"], False)
        out = io.StringIO()
        with redirect_stdout(out), self.assertRaises(SystemExit) as caught:
            matrix.main(["verify", "--help"])
        self.assertEqual(caught.exception.code, 0)
        self.assertIn("用法：", out.getvalue())
        self.assertNotIn("usage:", out.getvalue())


if __name__ == "__main__":
    unittest.main()
